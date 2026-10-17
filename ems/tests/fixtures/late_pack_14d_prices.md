# Synthetic 14-day prices for late-pack AC4 (#328)

Quarter-hour slots, 96 per local day, Europe/Amsterdam, 2026-10-05 through 2026-10-18
(no DST change in this range). Deterministic. No network and no production data.

Each hour below is stored as four identical 15-minute slots. The shape was written down
before the first planner run and is not fitted to the €/kWh result.

Tags: `oct5` = early medium-cheap hour plus a later deeper valley; `negative` = at least
one negative price; `flat` = no spread; `narrow` / `wide` / `classic` / `two-valley`
describe the spread.

| Date | Tags | Design |
|------|------|--------|
| 2026-10-05 | oct5 | 5 Oct pattern: early medium-cheap hour at 02:00 (€0.12), expensive gap, one-hour deep valley at 13:00 (€0.05) between dearer shoulders, evening peak €0.46–€0.52. |
| 2026-10-06 | oct5, negative | 5 Oct pattern with a negative later valley: early hour 02:00 at €0.09, expensive gap, 13:00 at €−0.04, evening peak still positive. |
| 2026-10-07 | flat | Flat spread: every quarter-hour is €0.18, no valley and no peak. |
| 2026-10-08 | classic | Classic night valley €0.05–€0.06 from 00:00–04:00, morning rise, evening peak €0.47. No isolated early spike. |
| 2026-10-09 | wide | Wide duck curve: long midday valley €0.02–€0.08 (10:00–15:00) and a high evening peak €0.58. |
| 2026-10-10 | narrow | Narrow spread: shallow midday dip €0.18 against a €0.26 evening high. |
| 2026-10-11 | oct5 | 5 Oct pattern shifted later: medium-cheap hour at 05:00 (€0.13), expensive morning, one-hour deep valley at 15:00 (€0.04). |
| 2026-10-12 | oct5 | Descending night into a medium hour at 06:00 (€0.14), then a late one-hour dip at 16:00 (€0.06) just before the peak. |
| 2026-10-13 | two-valley | Two valleys: a long early one (€0.07–€0.09) and a deeper midday one (€0.04–€0.06). The deep valley is long enough to hold a full charge window. |
| 2026-10-14 | negative | Negative night valley €−0.05 to €−0.02 from 00:00–03:00, cheapest and earliest block, with an ordinary evening peak. |
| 2026-10-15 | classic | Ordinary autumn shape: cheapest contiguous night €0.09–€0.11, moderate evening peak €0.48. |
| 2026-10-16 | oct5, negative, wide | Wide spread, 5 Oct pattern: early medium hour at 03:00 (€0.11) and a one-hour negative spike at 14:00 (€−0.08). |
| 2026-10-17 | narrow | Tight band €0.15–€0.30, mild evening crest, no deep valley. |
| 2026-10-18 | wide | Softer peaks (evening €0.36) and a long midday dip €0.04–€0.08. |

Hourly €/kWh (00:00–23:00), the source of the 96 slots:

- `2026-10-05`: 0.200, 0.180, 0.120, 0.330, 0.360, 0.310, 0.280, 0.380, 0.290, 0.240, 0.220, 0.210, 0.190, 0.050, 0.190, 0.210, 0.250, 0.460, 0.520, 0.490, 0.410, 0.270, 0.220, 0.190
- `2026-10-06`: 0.170, 0.150, 0.090, 0.280, 0.340, 0.300, 0.260, 0.360, 0.270, 0.230, 0.200, 0.180, 0.160, -0.040, 0.160, 0.190, 0.230, 0.440, 0.500, 0.470, 0.400, 0.250, 0.200, 0.170
- `2026-10-07`: 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180, 0.180
- `2026-10-08`: 0.060, 0.050, 0.050, 0.060, 0.080, 0.100, 0.160, 0.320, 0.280, 0.220, 0.180, 0.160, 0.140, 0.130, 0.140, 0.170, 0.220, 0.400, 0.470, 0.440, 0.360, 0.240, 0.160, 0.110
- `2026-10-09`: 0.140, 0.110, 0.100, 0.120, 0.180, 0.260, 0.330, 0.400, 0.320, 0.200, 0.080, 0.030, 0.020, 0.030, 0.070, 0.120, 0.220, 0.500, 0.580, 0.540, 0.420, 0.280, 0.180, 0.150
- `2026-10-10`: 0.200, 0.190, 0.185, 0.190, 0.200, 0.210, 0.220, 0.240, 0.230, 0.220, 0.210, 0.200, 0.190, 0.180, 0.185, 0.190, 0.210, 0.250, 0.260, 0.255, 0.240, 0.220, 0.210, 0.200
- `2026-10-11`: 0.220, 0.210, 0.200, 0.190, 0.160, 0.130, 0.340, 0.400, 0.360, 0.300, 0.260, 0.240, 0.220, 0.200, 0.230, 0.040, 0.230, 0.450, 0.530, 0.480, 0.390, 0.280, 0.220, 0.200
- `2026-10-12`: 0.240, 0.220, 0.210, 0.190, 0.170, 0.150, 0.140, 0.330, 0.370, 0.340, 0.300, 0.280, 0.260, 0.240, 0.220, 0.200, 0.060, 0.420, 0.550, 0.500, 0.430, 0.300, 0.240, 0.210
- `2026-10-13`: 0.080, 0.070, 0.090, 0.180, 0.280, 0.320, 0.300, 0.360, 0.270, 0.180, 0.120, 0.060, 0.040, 0.050, 0.100, 0.160, 0.220, 0.440, 0.510, 0.460, 0.380, 0.260, 0.180, 0.120
- `2026-10-14`: -0.020, -0.050, -0.030, 0.020, 0.080, 0.140, 0.220, 0.350, 0.300, 0.240, 0.180, 0.150, 0.120, 0.110, 0.130, 0.170, 0.240, 0.430, 0.490, 0.450, 0.360, 0.250, 0.160, 0.100
- `2026-10-15`: 0.120, 0.100, 0.090, 0.090, 0.110, 0.150, 0.220, 0.340, 0.300, 0.240, 0.190, 0.160, 0.140, 0.130, 0.150, 0.180, 0.260, 0.420, 0.480, 0.440, 0.350, 0.240, 0.170, 0.130
- `2026-10-16`: 0.190, 0.170, 0.140, 0.110, 0.290, 0.350, 0.380, 0.440, 0.360, 0.280, 0.220, 0.180, 0.160, 0.140, -0.080, 0.150, 0.210, 0.560, 0.620, 0.580, 0.470, 0.320, 0.240, 0.180
- `2026-10-17`: 0.160, 0.150, 0.150, 0.160, 0.170, 0.180, 0.200, 0.230, 0.220, 0.210, 0.190, 0.170, 0.160, 0.150, 0.160, 0.180, 0.210, 0.280, 0.300, 0.290, 0.260, 0.220, 0.190, 0.170
- `2026-10-18`: 0.130, 0.110, 0.100, 0.120, 0.140, 0.180, 0.240, 0.280, 0.220, 0.140, 0.080, 0.050, 0.040, 0.050, 0.070, 0.110, 0.160, 0.320, 0.360, 0.340, 0.270, 0.200, 0.150, 0.120

