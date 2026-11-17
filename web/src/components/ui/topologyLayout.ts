import type { Diagram } from './NetworkGraph';

export const deviceWidth = 168;
export const deviceHeight = 84;
const stepX = 210;
const stepY = 140;

export function expandedLayout(diagram: Diagram) {
  const nodes = new Map(diagram.nodes.map(node => [node.id, node]));
  const peers = new Map(diagram.nodes.map(node => [node.id, new Set<number>()]));
  for (const link of diagram.links) {
    peers.get(link.source)?.add(link.target);
    peers.get(link.target)?.add(link.source);
  }
  const queue = diagram.nodes.filter(node => /\bcore\b/i.test(node.label)).map(node => node.id);
  const owner = new Map(queue.map(id => [id, id]));
  function assignNeighbors(start: number) {
    for (let i = start; i < queue.length; i++) {
      for (const peer of peers.get(queue[i]) ?? []) {
        if (owner.has(peer)) continue;
        owner.set(peer, owner.get(queue[i])!);
        queue.push(peer);
      }
    }
  }
  assignNeighbors(0);
  if (diagram.findings) {
    const isNetworkDevice = (kind: string) => Number(['router', 'switch', 'firewall'].includes(kind));
    const hubs = diagram.nodes.filter(node => !owner.has(node.id) && peers.get(node.id)?.size).sort((a, b) =>
      isNetworkDevice(b.kind) - isNetworkDevice(a.kind)
      || (peers.get(b.id)?.size ?? 0) - (peers.get(a.id)?.size ?? 0) || a.label.localeCompare(b.label));
    for (const root of hubs) {
      if (owner.has(root.id)) continue;
      queue.push(root.id);
      owner.set(root.id, root.id);
      assignNeighbors(queue.length - 1);
    }
  }

  const byGroup = new Map<string, Diagram['nodes']>();
  for (const node of diagram.nodes) {
    const root = nodes.get(owner.get(node.id) ?? -1);
    const key = node.site_id ? `Site ${node.site_id}` : root?.label ?? (diagram.findings ? 'Unlinked devices' : 'Other devices');
    const group = byGroup.get(key) ?? [];
    group.push(node);
    byGroup.set(key, group);
  }
  const positions: Record<number, [number, number]> = {};
  const groupByNode = new Map<number, string>();
  const zones: { name: string; x: number; y: number; width: number; height: number }[] = [];
  let y = 28;
  for (const [name, group] of byGroup) {
    const columns = Math.min(5, Math.max(1, Math.ceil(Math.sqrt(group.length * 1.6))));
    const sorted = [...group].sort((a, b) => Number(owner.get(b.id) === b.id) - Number(owner.get(a.id) === a.id)
      || (peers.get(b.id)?.size ?? 0) - (peers.get(a.id)?.size ?? 0) || a.label.localeCompare(b.label));
    const zone = { name, x: 28, y, width: columns * stepX + 20, height: Math.ceil(group.length / columns) * stepY + 30 };
    zones.push(zone);
    sorted.forEach((node, index) => {
      groupByNode.set(node.id, name);
      positions[node.id] = [zone.x + 30 + index % columns * stepX, zone.y + 54 + Math.floor(index / columns) * stepY];
    });
    y += zone.height + 28;
  }
  return { positions, zones, groupByNode, width: Math.max(300, ...zones.map(zone => zone.x + zone.width + 28)), height: Math.max(320, y) };
}
