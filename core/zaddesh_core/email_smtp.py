"""SMTP client with verified TLS, sanitized outcomes, and explicit delivery ambiguity."""

from contextlib import contextmanager
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from ipaddress import ip_address
import smtplib
import socket
import ssl


@dataclass(frozen=True)
class SMTPOutcome:
    result: str
    message: str | None = None


class InvalidTarget(ValueError):
    pass


def smtp_address(host, port):
    addresses = set()
    for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        address = ip_address(row[4][0])
        actual = getattr(address, "ipv4_mapped", None) or address
        if (actual.is_link_local or actual.is_multicast or actual.is_unspecified or (actual.is_reserved and not actual.is_loopback)
                or str(actual) in {"168.63.129.16", "100.100.100.200", "fd00:ec2::254"}):
            raise InvalidTarget("SMTP destination is blocked")
        addresses.add(address)
    if not addresses:
        raise InvalidTarget("SMTP hostname returned no usable address")
    # Prefer IPv4 where both are published so a host with disabled IPv6 can still connect.
    return str(min(addresses, key=lambda item: (item.version, int(item))))


class PinnedSMTP(smtplib.SMTP):
    def __init__(self, config, context):
        self.address = smtp_address(config["host"], config["port"])
        self.tls = context if config["encryption"] == "tls" else None
        try:
            super().__init__(config["host"], config["port"], timeout=config["timeout"], local_hostname="zaddesh.local")
        except BaseException:
            self.close()
            raise

    def _get_socket(self, host, port, timeout):
        sock = socket.create_connection((self.address, port), timeout, self.source_address)
        if self.tls is None:
            return sock
        try:
            return self.tls.wrap_socket(sock, server_hostname=host)
        except BaseException:
            sock.close()
            raise


@contextmanager
def session(config, credential):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with_close = PinnedSMTP(config, context)
    try:
        with_close.ehlo_or_helo_if_needed()
        if config["encryption"] == "starttls":
            with_close.starttls(context=context)
            with_close.ehlo_or_helo_if_needed()
        if config["auth"] == "password":
            with_close.login(config["username"], credential)
        elif config["auth"] == "oauth2":
            auth = f'user={config["username"]}\x01auth=Bearer {credential}\x01\x01'
            with_close.auth("XOAUTH2", lambda challenge=None: auth if challenge is None else "")
        yield with_close
    finally:
        # The response to DATA is authoritative. A later QUIT failure must not trigger a resend.
        with_close.close()


def response_error(code, stage):
    return SMTPOutcome("temporary" if 400 <= code < 500 else "permanent", f"SMTP {stage} was rejected (code {code})")


def smtp_operation(config, credential, delivery=None):
    stage = "connection"
    try:
        with session(config, credential) as client:
            if delivery is None:
                return SMTPOutcome("connected")
            stage = "sender"
            code, _ = client.mail(config["sender"])
            if code != 250:
                return response_error(code, stage)
            stage = "recipient"
            code, _ = client.rcpt(delivery["address"])
            if code not in {250, 251, 252}:
                return response_error(code, stage)
            message = EmailMessage(policy=policy.SMTP)
            message["Subject"] = delivery["subject"]
            message["From"] = formataddr((config["sender_name"], config["sender"]))
            message["To"] = delivery["address"]
            message["Date"] = formatdate(delivery["created_at"], usegmt=True)
            message["Message-ID"] = f'<{delivery["dedup_key"]}@zaddesh.local>'
            if config["reply_to"]:
                message["Reply-To"] = config["reply_to"]
            message.set_content(delivery["payload"]["text"], cte="quoted-printable")
            if delivery["payload"]["html"]:
                message.add_alternative(delivery["payload"]["html"], subtype="html", cte="quoted-printable")
            stage = "message"
            code, _ = client.data(message.as_bytes())
            return SMTPOutcome("accepted") if code == 250 else response_error(code, stage)
    except smtplib.SMTPAuthenticationError as error:
        if 400 <= error.smtp_code < 500:
            return response_error(error.smtp_code, "authentication")
        return SMTPOutcome("authentication", f"SMTP rejected the authentication method or credential (code {error.smtp_code})")
    except smtplib.SMTPResponseException as error:
        return response_error(error.smtp_code, stage)
    except InvalidTarget:
        return SMTPOutcome("permanent", "SMTP destination is blocked or has no usable address")
    except ssl.SSLCertVerificationError:
        return SMTPOutcome("permanent", "SMTP TLS certificate verification failed")
    except ssl.SSLError:
        return SMTPOutcome("permanent", "Could not establish a verified SMTP TLS session")
    except smtplib.SMTPNotSupportedError:
        return SMTPOutcome("permanent", "SMTP server does not support the configured encryption or authentication")
    except (OSError, smtplib.SMTPServerDisconnected):
        if stage == "message":
            return SMTPOutcome("uncertain", "Connection ended during message transmission. Verify SMTP logs before retrying.")
        return SMTPOutcome("temporary", "Could not reach the SMTP server or the connection timed out")
    except (ValueError, smtplib.SMTPException):
        return SMTPOutcome("permanent", "SMTP configuration or message format was rejected")
