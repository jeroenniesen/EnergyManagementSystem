// A living time-of-day sky behind the top of the app: a gradient that fades from the sky colour into
// the theme background, with a sun arcing across the day (position by daylight progress) or a moon +
// stars at night. Driven by /api/sky (real sunrise/sunset for the home's location); falls back to
// clock-based phases if that's unavailable. Purely decorative (aria-hidden). Weather clouds arrive in
// a later loop. Re-evaluates the phase each minute so it transitions through dawn/day/dusk/night.
import { useEffect, useState } from "react";

type Sky = { now: string; sunrise: string | null; sunset: string | null };
type Phase = "night" | "dawn" | "day" | "dusk";

const TWILIGHT_MS = 45 * 60 * 1000;

const STARS = [
  { x: 14, y: 30, d: 0 }, { x: 27, y: 62, d: 1.3 }, { x: 43, y: 22, d: 0.6 },
  { x: 58, y: 50, d: 1.9 }, { x: 72, y: 30, d: 0.9 }, { x: 85, y: 60, d: 1.5 },
  { x: 35, y: 82, d: 2.2 }, { x: 66, y: 78, d: 0.35 }, { x: 91, y: 34, d: 1.1 },
];

function phaseAt(now: Date, sunrise: Date | null, sunset: Date | null): {
  phase: Phase;
  progress: number;
} {
  if (!sunrise || !sunset || Number.isNaN(sunrise.getTime()) || Number.isNaN(sunset.getTime())) {
    const h = now.getHours() + now.getMinutes() / 60; // clock fallback
    if (h < 6 || h >= 21) return { phase: "night", progress: 0.5 };
    if (h < 7.5) return { phase: "dawn", progress: 0.03 };
    if (h < 18) return { phase: "day", progress: (h - 7.5) / 10.5 };
    if (h < 21) return { phase: "dusk", progress: 0.97 };
    return { phase: "night", progress: 0.5 };
  }
  const t = now.getTime();
  const sr = sunrise.getTime();
  const ss = sunset.getTime();
  if (t < sr - TWILIGHT_MS || t > ss + TWILIGHT_MS) return { phase: "night", progress: 0.5 };
  if (t < sr + TWILIGHT_MS) return { phase: "dawn", progress: 0.03 };
  if (t > ss - TWILIGHT_MS) return { phase: "dusk", progress: 0.97 };
  return { phase: "day", progress: Math.max(0, Math.min(1, (t - sr) / (ss - sr))) };
}

export function SkyBackdrop() {
  const [sky, setSky] = useState<Sky | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    fetch("/api/sky")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("sky"))))
      .then((v: Sky) => {
        if (alive) setSky(v);
      })
      .catch(() => {
        /* fall back to clock phases */
      });
    const id = window.setInterval(() => {
      if (alive) setTick((n) => n + 1); // re-evaluate the phase every minute
    }, 60_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const now = new Date();
  const sunrise = sky?.sunrise ? new Date(sky.sunrise) : null;
  const sunset = sky?.sunset ? new Date(sky.sunset) : null;
  const { phase, progress } = phaseAt(now, sunrise, sunset);
  const daytime = phase !== "night";
  const sunLeft = 12 + progress * 76; // sweeps left→right across the day
  const sunTop = 214 - 168 * Math.sin(Math.PI * progress); // low at dawn/dusk, high at noon

  return (
    <div className={`sky sky-${phase}`} data-testid="sky" data-phase={phase} aria-hidden="true">
      <div className="sky-grad" />
      {daytime ? (
        <span className="sun" style={{ left: `${sunLeft}%`, top: `${sunTop}px` }} />
      ) : (
        <>
          <span className="moon" />
          <span className="stars">
            {STARS.map((s, i) => (
              <span
                key={i}
                className="star"
                style={{ left: `${s.x}%`, top: `${s.y}px`, animationDelay: `${s.d}s` }}
              />
            ))}
          </span>
        </>
      )}
    </div>
  );
}
