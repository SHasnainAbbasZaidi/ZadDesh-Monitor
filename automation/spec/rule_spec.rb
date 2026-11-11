# frozen_string_literal: true

require_relative '../lib/zaddesh_automation'

RSpec.describe ZadDesh::Automation::Rule do
  let(:source) do
    <<~RULE
      rule "High interface utilization" do
        on     metric("if.util.in", device: tagged("core"))
        when   avg(5.minutes) > 85
        severity :high
        notify  team("netops"), via: [:email, :slack]
        escalate after: 15.minutes, to: team("oncall")
        suppress_if parent_down?
      end
    RULE
  end

  it 'evaluates the example, excludes expired samples, and returns routing decisions' do
    rule = described_class.parse(source)
    result = rule.evaluate(metric: 'if.util.in', tags: ['core'], samples: [[699, 0], [990, 90], [1000, 88]],
                           now: 1000, parent_down: false)
    expect(result).to eq(rule: 'High interface utilization', severity: 'high', value: 89.0,
                         notify: { team: 'netops', channels: %w[email slack] },
                         escalation: { team: 'oncall', after_seconds: 900 })
  end

  it 'suppresses parent outages and ignores other tags and low values' do
    rule = described_class.parse(source)
    context = { metric: 'if.util.in', tags: ['core'], samples: [[1000, 90]], now: 1000, parent_down: false }
    expect(rule.evaluate(**context, parent_down: true)).to be_nil
    expect(rule.evaluate(**context, tags: ['other'])).to be_nil
    expect(rule.evaluate(**context, samples: [[1000, 84]])).to be_nil
  end

  [
    'File.read("/etc/passwd")', 'TCPSocket.new("127.0.0.1", 80)',
    'system("echo forbidden")', '`echo forbidden`', 'eval("1+1")', 'require "socket"',
    'ObjectSpace.each_object', 'Kernel.send(:system, "echo forbidden")'
  ].each do |escape|
    it "rejects executable syntax: #{escape.split(/[.( ]/).first}" do
      expect { described_class.parse(source.sub('suppress_if parent_down?', escape)) }
        .to raise_error(described_class::InvalidRule)
    end
  end
end
