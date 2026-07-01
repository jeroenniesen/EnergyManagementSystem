// A time-of-day landscape behind the top of the app — an illustrated scene (a house with solar
// panels, wind turbines, a distant city, and the sun or moon) that changes with the hour. Driven by
// /api/sky (real sunrise/sunset for the home's location); falls back to clock-based phases when that
// is unavailable. Purely decorative (aria-hidden). Re-evaluates the phase each minute.
import { useEffect, useState } from "react";

import dayImg from "./assets/sky/day.webp";
import duskImg from "./assets/sky/dusk.webp";
import nightImg from "./assets/sky/night.webp";

type Sky = { now: string; sunrise: string | null; sunset: string | null };
type Phase = "night" | "dawn" | "day" | "dusk";

// Three illustrated scenes drive four phases — dawn shares the warm dusk scene.
const SCENE: Record<Phase, string> = { night: nightImg, dawn: duskImg, day: dayImg, dusk: duskImg };

const TWILIGHT_MS = 45 * 60 * 1000;

function phaseAt(now: Date, sunrise: Date | null, sunset: Date | null): Phase {
  if (!sunrise || !sunset || Number.isNaN(sunrise.getTime()) || Number.isNaN(sunset.getTime())) {
    const h = now.getHours() + now.getMinutes() / 60; // clock fallback
    if (h < 6 || h >= 21) return "night";
    if (h < 7.5) return "dawn";
    if (h < 18) return "day";
    if (h < 21) return "dusk";
    return "night";
  }
  const t = now.getTime();
  const sr = sunrise.getTime();
  const ss = sunset.getTime();
  if (t < sr - TWILIGHT_MS || t > ss + TWILIGHT_MS) return "night";
  if (t < sr + TWILIGHT_MS) return "dawn";
  if (t > ss - TWILIGHT_MS) return "dusk";
  return "day";
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
  const phase = phaseAt(now, sunrise, sunset);

  return (
    <div
      className={`sky sky-${phase}`}
      data-testid="sky"
      data-phase={phase}
      aria-hidden="true"
      style={{ backgroundImage: `url(${SCENE[phase]})` }}
    />
  );
}
