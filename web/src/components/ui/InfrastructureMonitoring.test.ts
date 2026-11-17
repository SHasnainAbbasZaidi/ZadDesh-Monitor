import { describe, expect, it } from 'vitest';
import { usageScore } from './InfrastructureMonitoring';

describe('infrastructure usage', () => {
  it('renormalizes only measured resources and uses the rolling value', () => {
    const usage = usageScore({ cpu: { current: 70, value: 30, samples: 6 }, network: { current: 100, value: 100, samples: 4 } },
      { cpu: 40, memory: 30, network: 30 });
    expect(usage).toEqual({ score: 60, available: ['cpu', 'network'], total: 70 });
  });

  it('returns no score when measurements are missing', () => {
    expect(usageScore({}, { cpu: 40, memory: 30, network: 30 }).score).toBeNull();
  });
});
