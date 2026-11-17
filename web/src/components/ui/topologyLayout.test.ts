import { describe, expect, it } from 'vitest';
import type { Diagram } from './NetworkGraph';
import { deviceHeight, deviceWidth, expandedLayout } from './topologyLayout';

describe('expanded topology', () => {
  it('finds connected groups with no core name in Findings only', () => {
    const nodes = ['Host A', 'Switch A', 'Host B', 'Switch B', 'Spare'].map((label, i) =>
      ({ id: i + 1, label, kind: label.startsWith('Switch') ? 'switch' : 'server', status: 'up', alerts: 0 }));
    const links = [[1, 2], [3, 4]].map(([source, target], id) =>
      ({ id, source, target, source_port: '', target_port: '', method: 'lldp', status: 'up', confidence: 'high' }));
    const diagram: Diagram = { id: 1, name: 'Lab', published: false, layout: {}, nodes, links };
    expect(expandedLayout(diagram).zones.map(zone => zone.name)).toEqual(['Other devices']);
    const { groupByNode } = expandedLayout({ ...diagram, findings: true });
    expect([...groupByNode.values()]).toEqual(['Switch A', 'Switch A', 'Switch B', 'Switch B', 'Unlinked devices']);
  });

  it('separates core neighborhoods and leaves room around every device', () => {
    const nodes = ['Core Switch A', 'Core Switch B', 'Access A', 'Access B', 'Unlinked'].map((label, i) =>
      ({ id: i + 1, label, kind: 'switch', status: 'up', alerts: 0 }));
    const links = [[1, 3], [1, 2], [2, 4]].map(([source, target], id) =>
      ({ id, source, target, source_port: '', target_port: '', method: 'lldp', status: 'up', confidence: 'high' }));
    const diagram: Diagram = { id: 1, name: 'Lab', published: false, layout: {}, nodes, links };
    const { positions, zones } = expandedLayout(diagram);
    expect(zones.map(zone => zone.name)).toEqual(['Core Switch A', 'Core Switch B', 'Other devices']);
    expect(positions[1][1]).toBe(positions[3][1]);
    expect(positions[2][1]).toBe(positions[4][1]);
    const points = Object.values(positions);
    points.forEach(([x, y], index) => {
      for (const [nextX, nextY] of points.slice(index + 1)) {
        expect(Math.abs(x - nextX) > deviceWidth || Math.abs(y - nextY) > deviceHeight).toBe(true);
      }
    });
  });
});
