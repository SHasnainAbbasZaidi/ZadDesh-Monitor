export function formatMetricValue(value: number | null | undefined, unit = '') {
  if (value == null) return 'N/A';
  if (unit === 'bps') {
    const divisor = value >= 1e9 ? 1e9 : value >= 1e6 ? 1e6 : value >= 1e3 ? 1e3 : 1;
    return `${(value / divisor).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${divisor === 1e9 ? 'Gbps' : divisor === 1e6 ? 'Mbps' : divisor === 1e3 ? 'Kbps' : 'bps'}`;
  }
  if (unit === 'B' && value >= 1024) {
    const exponent = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
    return `${(value / 1024 ** exponent).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][exponent]}`;
  }
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ''}`;
}
