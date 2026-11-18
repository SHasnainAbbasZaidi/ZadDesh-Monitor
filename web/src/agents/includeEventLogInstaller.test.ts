import { describe, expect, it } from 'vitest';
import { includeEventLogInstaller } from './includeEventLogInstaller';
import windows from './setup-windows-snmp.ps1?raw';
import linux from './setup-linux-snmp.sh?raw';
import windowsUpdate from './enable-services-windows.ps1?raw';
import linuxUpdate from './enable-services-linux.sh?raw';

describe('SNMP client downloads', () => {
  it('includes the read-only Windows event reader in fresh and existing setups', () => {
    for (const template of [windows, windowsUpdate]) {
      const script = includeEventLogInstaller(template, 'windows');
      expect(script).toContain('Get-WinEvent');
      expect(script).toContain('extend zaddesh_events');
      expect(script).toContain('$eventsExtension');
      expect(script).not.toContain('# ZADDESH EVENT LOG INSTALL');
    }
  });
  it('writes Linux event configuration after agent.conf exists', () => {
    const fresh = includeEventLogInstaller(linux, 'linux');
    expect(fresh).toContain('journalctl');
    expect(fresh.indexOf('extend zaddesh_events')).toBeGreaterThan(fresh.indexOf('> "$config_dir/agent.conf"'));
    const update = includeEventLogInstaller(linuxUpdate, 'linux');
    expect(update).toContain('extend zaddesh_events');
    expect(update.indexOf('extend zaddesh_events')).toBeLessThan(update.lastIndexOf('systemctl restart'));
  });
});
