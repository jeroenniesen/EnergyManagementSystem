import Foundation

/// A single plotted point: its position in the original bucket array (so the x-axis keeps its
/// time spacing across gaps) and its value.
public struct SeriesPoint: Equatable, Sendable {
    public let index: Int
    public let value: Double

    public init(index: Int, value: Double) {
        self.index = index
        self.value = value
    }
}

/// Pure geometry for the Insights behavior charts. Kept free of SwiftUI so the "don't plot
/// missing/future samples as zero" rule (finding 3, mirroring the web `EnergyBehavior.tsx`) is
/// unit-tested directly.
public enum SeriesGeometry {
    /// Split a value series into contiguous runs of *sampled* buckets. A bucket is a gap when its
    /// `samples` count is 0 (missing or future) — those indices are omitted so the chart breaks the
    /// line instead of drawing a false zero. Indices are preserved so callers keep time spacing.
    /// A run of length 1 is an isolated point (draw a dot, not a line).
    /// `samples` shorter than `values` treats the tail as unsampled.
    public static func segments(values: [Double], samples: [Int]) -> [[SeriesPoint]] {
        var runs: [[SeriesPoint]] = []
        var current: [SeriesPoint] = []
        for index in values.indices {
            let sampled = index < samples.count && samples[index] > 0
            if sampled {
                current.append(SeriesPoint(index: index, value: values[index]))
            } else if !current.isEmpty {
                runs.append(current)
                current = []
            }
        }
        if !current.isEmpty { runs.append(current) }
        return runs
    }

    /// The connected runs (length >= 2) — drawn as polylines.
    public static func connectedRuns(values: [Double], samples: [Int]) -> [[SeriesPoint]] {
        segments(values: values, samples: samples).filter { $0.count >= 2 }
    }

    /// Lone sampled buckets between gaps (length-1 runs) — drawn as dots so a single point still
    /// shows without a line to nowhere.
    public static func isolatedPoints(values: [Double], samples: [Int]) -> [SeriesPoint] {
        segments(values: values, samples: samples).filter { $0.count == 1 }.compactMap(\.first)
    }

    /// Min/max across *sampled* buckets only, so a trailing run of future/empty buckets doesn't
    /// drag the axis toward zero. Returns nil when nothing is sampled.
    public static func sampledExtent(values: [Double], samples: [Int]) -> (min: Double, max: Double)? {
        let sampledValues = values.indices
            .filter { $0 < samples.count && samples[$0] > 0 }
            .map { values[$0] }
        guard let lo = sampledValues.min(), let hi = sampledValues.max() else { return nil }
        return (lo, hi)
    }
}
