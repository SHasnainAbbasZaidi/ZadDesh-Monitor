# frozen_string_literal: true

module ZadDesh
  module Automation
    VERSION = '0.1.0'

    # A declarative parser. Rule source is data and is never evaluated as Ruby.
    class Rule
      class InvalidRule < StandardError; end

      PATTERNS = [
        /\Arule "([a-zA-Z0-9 .:_-]{1,100})" do\z/,
        /\Aon\s+metric\("([a-zA-Z0-9._:-]{1,100})",\s*device:\s*tagged\("([a-zA-Z0-9._:-]{1,80})"\)\)\z/,
        /\Awhen\s+avg\(([1-9][0-9]{0,3})\.minutes\)\s*(>=|<=|>|<)\s*(\d{1,10}(?:\.\d{1,6})?)\z/,
        /\Aseverity\s+:(info|warning|high|critical)\z/,
        /\Anotify\s+team\("([a-zA-Z0-9._:-]{1,80})"\),\s*via:\s*\[([a-z:, ]{1,100})\]\z/,
        /\Aescalate\s+after:\s*([1-9][0-9]{0,3})\.minutes,\s*to:\s*team\("([a-zA-Z0-9._:-]{1,80})"\)\z/,
        /\Asuppress_if\s+parent_down\?\z/,
        /\Aend\z/
      ].freeze
      CHANNELS = %w[email webhook slack teams telegram discord].freeze
      COMPARISONS = {
        '>' => ->(a, b) { a > b }, '<' => ->(a, b) { a < b },
        '>=' => ->(a, b) { a >= b }, '<=' => ->(a, b) { a <= b }
      }.freeze

      def self.parse(source)
        raise InvalidRule, 'Expected a short UTF-8 rule' unless source.is_a?(String) && source.bytesize <= 8192

        lines = source.lines.map(&:strip).reject(&:empty?)
        raise InvalidRule, 'Unexpected rule structure' unless lines.length == PATTERNS.length

        new(PATTERNS.zip(lines).each_with_index.map { |(pattern, line), i| captures(pattern, line, i) })
      end

      def self.captures(pattern, line, index)
        match = pattern.match(line)
        raise InvalidRule, "Invalid declaration at line #{index + 1}" unless match

        match.captures
      end

      def initialize(captures)
        (@name,), (@metric, @tag), (window, @operator, threshold), (@severity,),
          (@notify, channels), (delay, @escalate) = captures
        @window_seconds = Integer(window) * 60
        @threshold = Float(threshold)
        @channels = channels.split(',').map { |channel| channel.strip.delete_prefix(':') }.uniq.freeze
        raise InvalidRule, 'Unsupported notification channel' if @channels.empty? || (@channels - CHANNELS).any?

        @delay_seconds = Integer(delay) * 60
      end

      # Samples are [epoch seconds, value] pairs from the metric named in the rule.
      # Evaluation returns a decision. Delivery is the caller's responsibility.
      def evaluate(metric:, tags:, samples:, now:, parent_down:)
        return unless eligible?(metric, tags, parent_down)

        average = window_average(samples, now)
        return unless average && COMPARISONS.fetch(@operator).call(average, @threshold)

        decision(average)
      end

      private

      def eligible?(metric, tags, parent_down)
        metric == @metric && tags.include?(@tag) && !parent_down
      end

      def window_average(samples, now)
        raise ArgumentError, 'At most 50,000 samples per evaluation' if samples.length > 50_000

        relevant = samples.filter_map do |timestamp, value|
          value if timestamp.between?(now - @window_seconds, now) && value.is_a?(Numeric) && value.finite?
        end
        relevant.sum.fdiv(relevant.length) unless relevant.empty?
      end

      def decision(average)
        {
          rule: @name, severity: @severity, value: average,
          notify: { team: @notify, channels: @channels.dup },
          escalation: { team: @escalate, after_seconds: @delay_seconds }
        }
      end
    end
  end
end
