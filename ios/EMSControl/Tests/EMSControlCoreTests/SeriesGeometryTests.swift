import XCTest
@testable import EMSControlCore

final class SeriesGeometryTests: XCTestCase {
    func testAllSampledIsOneConnectedRun() {
        let runs = SeriesGeometry.segments(values: [1, 2, 3], samples: [4, 4, 4])
        XCTAssertEqual(runs.count, 1)
        XCTAssertEqual(runs[0], [
            SeriesPoint(index: 0, value: 1),
            SeriesPoint(index: 1, value: 2),
            SeriesPoint(index: 2, value: 3),
        ])
        XCTAssertTrue(SeriesGeometry.isolatedPoints(values: [1, 2, 3], samples: [4, 4, 4]).isEmpty)
    }

    func testZeroSampleBucketBreaksTheLineInsteadOfPlottingZero() {
        // Middle bucket has no samples (missing) — must NOT be drawn as a 0; it splits the run into
        // two lone points, so nothing connects across the gap.
        let values = [1.0, 0.0, 3.0]
        let samples = [4, 0, 4]
        XCTAssertEqual(SeriesGeometry.segments(values: values, samples: samples).count, 2)
        XCTAssertTrue(SeriesGeometry.connectedRuns(values: values, samples: samples).isEmpty)
        XCTAssertEqual(SeriesGeometry.isolatedPoints(values: values, samples: samples),
                       [SeriesPoint(index: 0, value: 1), SeriesPoint(index: 2, value: 3)])
    }

    func testGapProducesTwoConnectedRuns() {
        let values = [1.0, 2.0, 0.0, 0.0, 5.0, 6.0]
        let samples = [4, 4, 0, 0, 4, 4]
        let connected = SeriesGeometry.connectedRuns(values: values, samples: samples)
        XCTAssertEqual(connected.count, 2)
        XCTAssertEqual(connected[0].map(\.index), [0, 1])
        XCTAssertEqual(connected[1].map(\.index), [4, 5])
    }

    func testLoneSampledBucketBetweenGapsIsAnIsolatedDot() {
        let values = [0.0, 5.0, 0.0]
        let samples = [0, 3, 0]
        XCTAssertTrue(SeriesGeometry.connectedRuns(values: values, samples: samples).isEmpty)
        XCTAssertEqual(SeriesGeometry.isolatedPoints(values: values, samples: samples),
                       [SeriesPoint(index: 1, value: 5)])
    }

    func testTrailingFutureBucketsAreOmitted() {
        // A day still in progress: later buckets have no samples yet.
        let values = [1.0, 2.0, 0.0, 0.0]
        let samples = [4, 4, 0, 0]
        let connected = SeriesGeometry.connectedRuns(values: values, samples: samples)
        XCTAssertEqual(connected.count, 1)
        XCTAssertEqual(connected[0].map(\.index), [0, 1])
    }

    func testSamplesShorterThanValuesTreatsTailAsUnsampled() {
        let runs = SeriesGeometry.segments(values: [1, 2, 3], samples: [4])
        XCTAssertEqual(runs.count, 1)
        XCTAssertEqual(runs[0].map(\.index), [0])
    }

    func testSampledExtentIgnoresUnsampledZeros() {
        // Future zeros must not drag the axis minimum below the real sampled minimum.
        let ext = SeriesGeometry.sampledExtent(values: [3.0, 5.0, 0.0], samples: [4, 4, 0])
        XCTAssertEqual(ext?.min, 3.0)
        XCTAssertEqual(ext?.max, 5.0)
    }

    func testSampledExtentNilWhenNothingSampled() {
        XCTAssertNil(SeriesGeometry.sampledExtent(values: [0, 0, 0], samples: [0, 0, 0]))
    }
}
