// A living time-of-day sky behind the top of the app: a gradient that fades from the sky colour into
// the theme background, with a sun arcing across the day (position by daylight progress) or a moon +
// stars at night. Driven by /api/sky (real sunrise/sunset for the home's location); falls back to
// clock-based phases if that's unavailable. Purely decorative (aria-hidden). Weather clouds arrive in
// a later loop. Re-evaluates the phase each minute so it transitions through dawn/day/dusk/night.
import { type CSSProperties, useEffect, useState } from "react";

type Sky = {
  now: string;
  sunrise: string | null;
  sunset: string | null;
  cloud_cover: number | null;
};
type Phase = "night" | "dawn" | "day" | "dusk";

// Centres kept below the header scrim (~110px) so a cloud never drifts across the title/nav —
// they sit in the open sky band, like the sun's arc.
const CLOUDS = [
  { x: 14, y: 152, w: 118 },
  { x: 50, y: 150, w: 150 },
  { x: 80, y: 170, w: 96 },
];

function Cloud({ style }: { style: CSSProperties }) {
  return (
    <svg className="cloud" viewBox="0 0 100 58" style={style} data-testid="cloud" aria-hidden="true">
      <g fill="currentColor">
        <ellipse cx="50" cy="42" rx="44" ry="15" />
        <circle cx="34" cy="34" r="17" />
        <circle cx="55" cy="27" r="22" />
        <circle cx="72" cy="37" r="15" />
      </g>
    </svg>
  );
}

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
  // Arc kept below the header scrim (peaks ~118px at noon) so the sun stays visible in the open sky
  // band rather than hiding behind the title/nav; low near the horizon at dawn/dusk.
  const sunTop = 216 - 98 * Math.sin(Math.PI * progress);

  const cloud = sky?.cloud_cover ?? null;
  const showClouds = daytime && cloud != null && cloud >= 35;
  const cloudCount = cloud == null ? 0 : cloud > 75 ? 3 : cloud > 55 ? 2 : 1;
  const cloudOpacity = cloud == null ? 0.6 : Math.min(0.96, 0.55 + cloud / 220);
  const sunDim = cloud != null && cloud > 70; // the sun peeks through / hides on an overcast day

  return (
    <div
      className={`sky sky-${phase}${showClouds ? " sky-cloudy" : ""}`}
      data-testid="sky"
      data-phase={phase}
      aria-hidden="true"
    >
      <div className="sky-grad" />
      {daytime ? (
        <span
          className={`sun${sunDim ? " sun-dim" : ""}`}
          style={{ left: `${sunLeft}%`, top: `${sunTop}px` }}
        />
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
      {showClouds &&
        CLOUDS.slice(0, cloudCount).map((c, i) => (
          <Cloud key={i} style={{ left: `${c.x}%`, top: `${c.y}px`, width: c.w, opacity: cloudOpacity }} />
        ))}
    </div>
  );
}
