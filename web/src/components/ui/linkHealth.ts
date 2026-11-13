export function linkHealth(link: { status: string; speed?: number | null; expected_speed?: number | null }) {
  if (link.status === 'down') return { label: 'Down', tone: 'down', color: 'var(--ops-down)' };
  if (link.status === 'up') {
    if (link.speed && link.expected_speed && link.speed < link.expected_speed) {
      return { label: 'Slow', tone: 'warning', color: 'var(--ops-warning)' };
    }
    return { label: 'Online', tone: 'up', color: 'var(--ops-up)' };
  }
  return { label: 'Unknown', tone: 'unknown', color: 'var(--ops-muted)' };
}
