import { useLayoutEffect, useRef } from 'react';

export function RollingCount({ value }: { value?: number }) {
  const label = value?.toLocaleString() ?? '—';
  const previous = useRef(label);
  const digits = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    const before = previous.current;
    previous.current = label;
    if (before === label || before === '—' || label === '—' ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const previousDigits = [...before].reverse();
    const animations: Animation[] = [];
    const elements = Array.from(digits.current?.children ?? []).reverse();
    elements.forEach((element, index) => {
      const digit = element.textContent ?? '';
      if (!/\p{Decimal_Number}/u.test(digit) || digit === previousDigits[index] || !element.animate) return;
      animations.push(element.animate([
        { transform: 'translateY(.35em)', opacity: .7 },
        { transform: 'translateY(0)', opacity: 1 },
      ], { duration: 220, delay: animations.length * 100, easing: 'cubic-bezier(.2,.65,.3,1)' }));
    });
    return () => animations.forEach(animation => animation.cancel());
  }, [label]);

  return <strong className="ops-count">
    <span className="sr-only">{label}</span>
    <span ref={digits} className="ops-count-digits" aria-hidden="true">
      {[...label].map((character, index) => <span key={index}>{character}</span>)}
    </span>
  </strong>;
}
