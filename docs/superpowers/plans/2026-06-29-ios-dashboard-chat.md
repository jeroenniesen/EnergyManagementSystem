# iOS Dashboard and Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a native iOS Dashboard + Chat app for EMS, backed by a cached `/api/dashboard` server endpoint so mobile/web/future monitors do not multiply device, external API, or LLM calls.

**Architecture:** Add a versioned FastAPI dashboard read model first, protected by a short server-side snapshot cache. Build the iOS code as a testable Swift core package plus SwiftUI app target: `APIClient`, `DashboardStore`, `ChatStore`, `ServerDiscovery`, `DemoDataStore`, `Theme`, and views consume typed DTOs and never duplicate planner logic.

**Tech Stack:** Python 3.12, FastAPI, pytest, Swift 6, SwiftUI, XCTest, URLSession, Keychain Security framework, SF Symbols, Xcode/iOS Simulator.

## Global Constraints

- First iteration scope is dashboard, server connection, and chat only.
- No cloud relay, hosted login, public internet access, or third-party tunneling.
- No battery write controls in the iOS app.
- Server remains the source of truth; no planner logic in the iOS app.
- `/api/dashboard` is additive and must not remove existing granular endpoints.
- `/api/dashboard` must return `api_version: 1`, `generated_at`, `server_time`, `server_name`, `cache_ttl_seconds`, `degraded_sections`, and stable top-level sections.
- Multiple clients inside the dashboard TTL must reuse one server snapshot.
- iOS must not poll many granular dashboard endpoints in a loop.
- Chat history is session-only in v1 and is not persisted locally.
- Demo mode must work without a live server and be clearly labeled.
- The iOS app uses the web color scheme: dark `#0b0e13`, `#161b23`, `#1e242e`, `#2a313c`, `#e6e9ef`, `#8b95a5`, `#46c8a8`, `#e0a23a`, `#f4b0b0`, `#5aa2e0`; light `#eef1f6`, `#ffffff`, `#f1f4f9`, `#e2e7ef`, `#1b2330`, `#5c6675`, `#1f9e84`, `#b07410`, `#c0392b`, `#2f7fc4`.
- Liquid Glass is used for navigation/sheets/overlays only; critical telemetry stays high-contrast and mostly opaque.
- Each iteration leaves explicit evidence: passing backend/iOS test output plus validation screenshots or notes for the screens touched in that iteration.

---

## File Structure

Backend:

- Create `ems/web/dashboard.py` - pure dashboard snapshot builder and short TTL cache wrapper.
- Modify `ems/web/api.py` - mount `GET /api/dashboard` and call the builder off the event loop.
- Create `ems/tests/test_dashboard_api.py` - endpoint contract, TTL, degraded section, and source-read coalescing tests.
- Update `docs/api-reference.md` - document `/api/dashboard` v1 contract.

iOS:

- Create `ios/EMSControl/Package.swift` - Swift package for testable core code and a lightweight app source layout.
- Create `ios/EMSControl/Sources/EMSControlCore/Models.swift` - Codable DTOs for dashboard/auth/chat/FAQ.
- Create `ios/EMSControl/Sources/EMSControlCore/APIClient.swift` - URLSession API client and auth header handling.
- Create `ios/EMSControl/Sources/EMSControlCore/DashboardStore.swift` - refresh state, TTL scheduling metadata, stale snapshot handling.
- Create `ios/EMSControl/Sources/EMSControlCore/ChatStore.swift` - session-only chat state.
- Create `ios/EMSControl/Sources/EMSControlCore/ServerDiscovery.swift` - manual URL and QR payload parsing, Bonjour model surface.
- Create `ios/EMSControl/Sources/EMSControlCore/DemoDataStore.swift` - bundled demo fixtures.
- Create `ios/EMSControl/Sources/EMSControlCore/Theme.swift` - color tokens.
- Create `ios/EMSControl/Sources/EMSControlApp/*.swift` - SwiftUI app shell, connection, dashboard, chat, reusable cards.
- Create `ios/EMSControl/Tests/EMSControlCoreTests/*.swift` - XCTest unit tests for models/stores/client/theme.
- Create `ios/EMSControl/Resources/demo-dashboard.json`, `demo-faq.json`, `demo-chat.json` - clearly synthetic fixtures.
- Create `ios/EMSControl/README.md` - local build/test/reviewer notes.
- Create `docs/ios-validation/` - validation screenshots/notes per iteration.
- Create `ios/EMSControl/EMSControl.xcodeproj` in Task 5 if it is not generated earlier by Xcode; it wraps the Swift package/app sources for simulator/App Store builds.

---

### Task 1: Backend `/api/dashboard` Snapshot Contract

**Files:**
- Create: `ems/web/dashboard.py`
- Modify: `ems/web/api.py`
- Test: `ems/tests/test_dashboard_api.py`
- Modify: `docs/api-reference.md`

**Interfaces:**
- Consumes existing endpoint helper data in `ems/web/api.py`: `_readiness(now)`, `freshness.snapshot(now)`, `alerts_endpoint()`, `decision_endpoint()`, `battery_endpoint()`, `charge_need_endpoint()`, `savings_endpoint()`, `energy_story()`, `strategy_endpoint()`, `status()`, `ai_validation_latest()`.
- Produces `GET /api/dashboard -> dict` with top-level keys `api_version`, `generated_at`, `server_time`, `server_name`, `cache_ttl_seconds`, `degraded_sections`, `readiness`, `status`, `freshness`, `strategy`, `decision`, `alerts`, `battery`, `charge_need`, `savings`, `energy_story`, `ai_validation`.
- Produces `DashboardSnapshotCache(ttl_seconds: int, clock: Callable[[], datetime])` with `get_or_build(build: Callable[[], dict]) -> dict`.

- [ ] **Step 1: Write failing endpoint contract tests**

Create `ems/tests/test_dashboard_api.py` with:

```python
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


class CountingSource:
    def __init__(self) -> None:
        self.reads = 0

    def read(self) -> RawSample:
        self.reads += 1
        return RawSample(
            grid_power_w=120.0,
            solar_power_w=450.0,
            battery_power_w=80.0,
            ev_power_w=0.0,
            soc_pct=64.0,
        )


def app_for(tmp_path, source):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        source,
        dry_run=True,
        dev_mode="mock",
        tz=AMS,
        store=HistoryStore(db),
        price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
    )


def test_dashboard_returns_versioned_top_level_contract(tmp_path):
    src = CountingSource()
    client = TestClient(app_for(tmp_path, src))

    body = client.get("/api/dashboard").json()

    assert body["api_version"] == 1
    assert body["cache_ttl_seconds"] == 10
    assert body["server_name"] == "Home EMS"
    assert body["degraded_sections"] == []
    for key in (
        "generated_at",
        "server_time",
        "readiness",
        "status",
        "freshness",
        "strategy",
        "decision",
        "alerts",
        "battery",
        "charge_need",
        "savings",
        "energy_story",
        "ai_validation",
    ):
        assert key in body


def test_dashboard_snapshot_is_reused_inside_ttl(tmp_path):
    src = CountingSource()
    client = TestClient(app_for(tmp_path, src))

    first = client.get("/api/dashboard").json()
    second = client.get("/api/dashboard").json()

    assert second["generated_at"] == first["generated_at"]
    assert src.reads == 1


def test_concurrent_dashboard_requests_share_one_snapshot(tmp_path):
    src = CountingSource()
    client = TestClient(app_for(tmp_path, src))

    def fetch():
        return client.get("/api/dashboard").json()["generated_at"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        stamps = list(pool.map(lambda _: fetch(), range(8)))

    assert len(set(stamps)) == 1
    assert src.reads == 1


def test_dashboard_degrades_section_instead_of_failing_response(tmp_path, monkeypatch):
    import ems.web.api as api

    src = CountingSource()
    client = TestClient(app_for(tmp_path, src))

    def boom(*args, **kwargs):
        raise RuntimeError("battery unavailable")

    monkeypatch.setattr(api, "battery_payload", boom, raising=False)
    body = client.get("/api/dashboard").json()

    assert "battery" in body["degraded_sections"]
    assert body["battery"]["state"] == "degraded"
    assert "temporarily unavailable" in body["battery"]["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
./.venv/bin/pytest ems/tests/test_dashboard_api.py -q
```

Expected: tests fail because `/api/dashboard` and `ems.web.dashboard` do not exist yet.

- [ ] **Step 3: Add the snapshot cache helper**

Create `ems/web/dashboard.py`:

```python
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

DASHBOARD_API_VERSION = 1
DASHBOARD_CACHE_TTL_SECONDS = 10


def degraded_section(message: str, now: datetime) -> dict[str, Any]:
    return {
        "state": "degraded",
        "message": message,
        "updated_at": now.isoformat(),
    }


class DashboardSnapshotCache:
    def __init__(
        self,
        ttl_seconds: int = DASHBOARD_CACHE_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] | None = None
        self._at: datetime | None = None

    def get_or_build(self, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        now = self._clock()
        if self._fresh(now):
            return dict(self._snapshot or {})
        with self._lock:
            now = self._clock()
            if self._fresh(now):
                return dict(self._snapshot or {})
            snapshot = build()
            self._snapshot = snapshot
            self._at = now
            return dict(snapshot)

    def _fresh(self, now: datetime) -> bool:
        return (
            self._snapshot is not None
            and self._at is not None
            and (now - self._at).total_seconds() < self.ttl_seconds
        )


def dashboard_shell(now: datetime, server_name: str = "Home EMS") -> dict[str, Any]:
    stamp = now.isoformat()
    return {
        "api_version": DASHBOARD_API_VERSION,
        "generated_at": stamp,
        "server_time": stamp,
        "server_name": server_name or "Home EMS",
        "cache_ttl_seconds": DASHBOARD_CACHE_TTL_SECONDS,
        "degraded_sections": [],
    }
```

- [ ] **Step 4: Mount `GET /api/dashboard` in `ems/web/api.py`**

Add imports near the other `ems.web` imports:

```python
from ems.web.dashboard import (
    DASHBOARD_CACHE_TTL_SECONDS,
    DashboardSnapshotCache,
    dashboard_shell,
    degraded_section,
)
```

Inside `create_app`, near the live sample caches, add:

```python
    _dashboard_cache = DashboardSnapshotCache(DASHBOARD_CACHE_TTL_SECONDS)
```

Add a local helper before route declarations:

```python
    def _dashboard_snapshot_sync(now: datetime) -> dict:
        out = dashboard_shell(now, server_name="Home EMS")
        degraded: list[str] = []

        def section(name: str, build):
            try:
                return build()
            except Exception:
                degraded.append(name)
                return degraded_section(f"{name.replace('_', ' ').title()} is temporarily unavailable.", now)

        out.update({
            "readiness": section("readiness", lambda: _readiness(now).to_dict()),
            "status": section("status", status),
            "freshness": section(
                "freshness",
                lambda: freshness.snapshot(now) if freshness is not None else {},
            ),
            "strategy": section("strategy", strategy_endpoint),
            "decision": section("decision", lambda: {"state": "ok", "message": "Open /api/decision for explained details."}),
            "alerts": section("alerts", alerts_endpoint),
            "battery": section("battery", battery_payload),
            "charge_need": section("charge_need", charge_need_endpoint),
            "savings": section("savings", savings_endpoint),
            "energy_story": section("energy_story", lambda: {"state": "ok", "message": "Open /api/energy-story for timeline details."}),
            "ai_validation": section("ai_validation", ai_validation_latest),
        })
        out["degraded_sections"] = degraded
        return out
```

Add the route:

```python
    @app.get("/api/dashboard")
    async def dashboard_endpoint() -> dict:
        return await asyncio.to_thread(
            _dashboard_cache.get_or_build,
            lambda: _dashboard_snapshot_sync(datetime.now(UTC)),
        )
```

If `battery_payload`, `strategy_endpoint`, or `savings_endpoint` are nested below this helper, move only the route definition below those functions so all referenced names are defined before requests execute. Keep the helper itself small and sync; never call live devices directly outside existing coalesced helpers.

- [ ] **Step 5: Run tests and fix route-order issues**

Run:

```bash
./.venv/bin/pytest ems/tests/test_dashboard_api.py ems/tests/test_read_coalescing.py ems/tests/test_explainer_api.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Document `/api/dashboard`**

Append to `docs/api-reference.md`:

```markdown
## EMS mobile dashboard

- `GET /api/dashboard` returns the versioned native/mobile dashboard read model.
- Contract version: `api_version: 1`.
- Server snapshot cache: `cache_ttl_seconds` defaults to 10 seconds. Multiple clients inside this window reuse one server-built snapshot.
- Required top-level keys: `generated_at`, `server_time`, `server_name`, `degraded_sections`, `readiness`, `status`, `freshness`, `strategy`, `decision`, `alerts`, `battery`, `charge_need`, `savings`, `energy_story`, `ai_validation`.
- Section failures return a degraded object with `state`, `message`, and `updated_at`; sections are not omitted.
```

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add ems/web/dashboard.py ems/web/api.py ems/tests/test_dashboard_api.py docs/api-reference.md
git commit -m "feat: add cached dashboard snapshot api"
```

---

### Task 2: iOS Core Package, Models, Theme, and Demo Fixtures

**Files:**
- Create: `ios/EMSControl/Package.swift`
- Create: `ios/EMSControl/Sources/EMSControlCore/Models.swift`
- Create: `ios/EMSControl/Sources/EMSControlCore/Theme.swift`
- Create: `ios/EMSControl/Sources/EMSControlCore/DemoDataStore.swift`
- Create: `ios/EMSControl/Resources/demo-dashboard.json`
- Create: `ios/EMSControl/Resources/demo-faq.json`
- Create: `ios/EMSControl/Resources/demo-chat.json`
- Create: `ios/EMSControl/Tests/EMSControlCoreTests/ModelsTests.swift`
- Create: `ios/EMSControl/Tests/EMSControlCoreTests/ThemeTests.swift`
- Create: `ios/EMSControl/Tests/EMSControlCoreTests/DemoDataStoreTests.swift`

**Interfaces:**
- Consumes `/api/dashboard` v1 JSON from Task 1.
- Produces `DashboardSnapshot`, `SectionState`, `FAQResponse`, `ChatResponse`, `ExplainerStatus`, `EMSTheme`, `DemoDataStore`.

- [ ] **Step 1: Create Swift package manifest**

Create `ios/EMSControl/Package.swift`:

```swift
// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "EMSControl",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "EMSControlCore", targets: ["EMSControlCore"])
    ],
    targets: [
        .target(
            name: "EMSControlCore",
            resources: [.process("../../Resources")]
        ),
        .testTarget(
            name: "EMSControlCoreTests",
            dependencies: ["EMSControlCore"]
        )
    ]
)
```

- [ ] **Step 2: Write failing model/theme/demo tests**

Create `ios/EMSControl/Tests/EMSControlCoreTests/ModelsTests.swift`:

```swift
import XCTest
@testable import EMSControlCore

final class ModelsTests: XCTestCase {
    func testDashboardSnapshotDecodesVersionedContract() throws {
        let json = """
        {
          "api_version": 1,
          "generated_at": "2026-06-29T12:00:00+00:00",
          "server_time": "2026-06-29T12:00:01+00:00",
          "server_name": "Home EMS",
          "cache_ttl_seconds": 10,
          "degraded_sections": ["battery"],
          "readiness": {"dashboard_ready": true},
          "status": {"soc_pct": 64.0},
          "freshness": {},
          "strategy": {},
          "decision": {},
          "alerts": {"alerts": []},
          "battery": {"state": "degraded", "message": "Battery details are temporarily unavailable.", "updated_at": "2026-06-29T12:00:00+00:00"},
          "charge_need": {},
          "savings": {},
          "energy_story": {},
          "ai_validation": {"latest": null, "active": false}
        }
        """.data(using: .utf8)!

        let snapshot = try JSONDecoder.ems.decode(DashboardSnapshot.self, from: json)

        XCTAssertEqual(snapshot.apiVersion, 1)
        XCTAssertEqual(snapshot.serverName, "Home EMS")
        XCTAssertEqual(snapshot.cacheTTLSeconds, 10)
        XCTAssertEqual(snapshot.degradedSections, ["battery"])
        XCTAssertEqual(snapshot.battery.state, .degraded)
    }
}
```

Create `ios/EMSControl/Tests/EMSControlCoreTests/ThemeTests.swift`:

```swift
import XCTest
@testable import EMSControlCore

final class ThemeTests: XCTestCase {
    func testThemeTokensMatchWebPalette() {
        XCTAssertEqual(EMSTheme.dark.background.hex, "#0b0e13")
        XCTAssertEqual(EMSTheme.dark.panel.hex, "#161b23")
        XCTAssertEqual(EMSTheme.dark.accent.hex, "#46c8a8")
        XCTAssertEqual(EMSTheme.light.background.hex, "#eef1f6")
        XCTAssertEqual(EMSTheme.light.panel.hex, "#ffffff")
        XCTAssertEqual(EMSTheme.light.accent.hex, "#1f9e84")
    }
}
```

Create `ios/EMSControl/Tests/EMSControlCoreTests/DemoDataStoreTests.swift`:

```swift
import XCTest
@testable import EMSControlCore

final class DemoDataStoreTests: XCTestCase {
    func testDemoDashboardLoadsAndIsMarkedDemo() throws {
        let store = DemoDataStore(bundle: .module)
        let snapshot = try store.dashboardSnapshot()
        XCTAssertEqual(snapshot.serverName, "Demo Home EMS")
        XCTAssertTrue(snapshot.isDemo)
    }
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
cd ios/EMSControl
swift test
```

Expected: fails because `EMSControlCore` source files do not exist.

- [ ] **Step 4: Implement models**

Create `ios/EMSControl/Sources/EMSControlCore/Models.swift`:

```swift
import Foundation

public extension JSONDecoder {
    static var ems: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}

public extension JSONEncoder {
    static var ems: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }
}

public enum SectionState: String, Codable, Equatable {
    case ok
    case stale
    case degraded
    case unavailable
}

public struct FlexibleSection: Codable, Equatable {
    public let state: SectionState?
    public let message: String?
    public let updatedAt: Date?
    public let values: [String: JSONValue]

    public init(state: SectionState? = nil, message: String? = nil, updatedAt: Date? = nil, values: [String: JSONValue] = [:]) {
        self.state = state
        self.message = message
        self.updatedAt = updatedAt
        self.values = values
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: DynamicCodingKey.self)
        var values: [String: JSONValue] = [:]
        for key in container.allKeys {
            values[key.stringValue] = try container.decode(JSONValue.self, forKey: key)
        }
        self.values = values
        self.state = try values["state"]?.string.flatMap(SectionState.init(rawValue:))
        self.message = values["message"]?.string
        if let raw = values["updated_at"]?.string {
            self.updatedAt = ISO8601DateFormatter().date(from: raw)
        } else {
            self.updatedAt = nil
        }
    }
}

public struct DashboardSnapshot: Codable, Equatable {
    public let apiVersion: Int
    public let generatedAt: Date
    public let serverTime: Date
    public let serverName: String
    public let cacheTTLSeconds: Int
    public let degradedSections: [String]
    public let readiness: FlexibleSection
    public let status: FlexibleSection
    public let freshness: FlexibleSection
    public let strategy: FlexibleSection
    public let decision: FlexibleSection
    public let alerts: FlexibleSection
    public let battery: FlexibleSection
    public let chargeNeed: FlexibleSection
    public let savings: FlexibleSection
    public let energyStory: FlexibleSection
    public let aiValidation: FlexibleSection?

    public var isDemo: Bool { serverName.lowercased().contains("demo") }
}

public struct FAQItem: Codable, Equatable, Identifiable {
    public let key: String
    public let question: String
    public let answer: String
    public var id: String { key }
}

public struct FAQResponse: Codable, Equatable {
    public let aiOn: Bool
    public let items: [FAQItem]
}

public struct ChatRequest: Codable, Equatable {
    public let question: String
}

public struct ChatResponse: Codable, Equatable {
    public let answer: String
    public let source: String
}

public struct ExplainerStatus: Codable, Equatable {
    public let mode: String
    public let active: Bool
    public let language: String
}

public enum JSONValue: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public var string: String? {
        if case let .string(value) = self { return value }
        return nil
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([String: JSONValue].self) { self = .object(value) }
        else { self = .array(try container.decode([JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}

public struct DynamicCodingKey: CodingKey {
    public let stringValue: String
    public let intValue: Int?

    public init(stringValue: String) {
        self.stringValue = stringValue
        self.intValue = nil
    }

    public init?(intValue: Int) {
        self.stringValue = "\(intValue)"
        self.intValue = intValue
    }
}
```

- [ ] **Step 5: Implement theme tokens**

Create `ios/EMSControl/Sources/EMSControlCore/Theme.swift`:

```swift
import Foundation

public struct HexColor: Equatable {
    public let hex: String
    public init(_ hex: String) { self.hex = hex.lowercased() }
}

public struct EMSTheme: Equatable {
    public let background: HexColor
    public let panel: HexColor
    public let secondaryPanel: HexColor
    public let line: HexColor
    public let text: HexColor
    public let muted: HexColor
    public let accent: HexColor
    public let amber: HexColor
    public let error: HexColor
    public let winter: HexColor

    public static let dark = EMSTheme(
        background: HexColor("#0b0e13"),
        panel: HexColor("#161b23"),
        secondaryPanel: HexColor("#1e242e"),
        line: HexColor("#2a313c"),
        text: HexColor("#e6e9ef"),
        muted: HexColor("#8b95a5"),
        accent: HexColor("#46c8a8"),
        amber: HexColor("#e0a23a"),
        error: HexColor("#f4b0b0"),
        winter: HexColor("#5aa2e0")
    )

    public static let light = EMSTheme(
        background: HexColor("#eef1f6"),
        panel: HexColor("#ffffff"),
        secondaryPanel: HexColor("#f1f4f9"),
        line: HexColor("#e2e7ef"),
        text: HexColor("#1b2330"),
        muted: HexColor("#5c6675"),
        accent: HexColor("#1f9e84"),
        amber: HexColor("#b07410"),
        error: HexColor("#c0392b"),
        winter: HexColor("#2f7fc4")
    )
}
```

- [ ] **Step 6: Add demo fixtures and loader**

Create `ios/EMSControl/Resources/demo-dashboard.json`:

```json
{
  "api_version": 1,
  "generated_at": "2026-06-29T12:00:00+00:00",
  "server_time": "2026-06-29T12:00:00+00:00",
  "server_name": "Demo Home EMS",
  "cache_ttl_seconds": 10,
  "degraded_sections": [],
  "readiness": {"dashboard_ready": true, "control_ready": false},
  "status": {"soc_pct": 64.0, "dry_run": true, "solar_power_w": 1450.0, "grid_power_w": -320.0},
  "freshness": {"battery": "fresh", "prices": "fresh", "forecast": "fresh"},
  "strategy": {"mode": "summer", "reason": "Demo plan: hold enough energy for tonight."},
  "decision": {"intent": "allow_self_consumption", "plan_reason": "Demo data: solar is covering the house now."},
  "alerts": {"alerts": []},
  "battery": {"aggregate": {"online_towers": 2}, "state": "ok"},
  "charge_need": {"target_soc_pct": 72.0, "target_kwh": 7.9},
  "savings": {"today_eur": 1.82},
  "energy_story": {"headline": "Demo: enough battery for the evening plan."},
  "ai_validation": {"latest": null, "active": false}
}
```

Create `ios/EMSControl/Resources/demo-faq.json`:

```json
{
  "ai_on": false,
  "items": [
    {
      "key": "battery_safe",
      "question": "Is my battery safe?",
      "answer": "Demo data: the EMS is observing only and would leave the battery in self-consumption."
    }
  ]
}
```

Create `ios/EMSControl/Resources/demo-chat.json`:

```json
{
  "answer": "Demo answer: the battery is shown as holding enough charge for tonight. Connect your EMS server for live grounded answers.",
  "source": "demo"
}
```

Create `ios/EMSControl/Sources/EMSControlCore/DemoDataStore.swift`:

```swift
import Foundation

public struct DemoDataStore {
    private let bundle: Bundle

    public init(bundle: Bundle = .module) {
        self.bundle = bundle
    }

    public func dashboardSnapshot() throws -> DashboardSnapshot {
        try decode(DashboardSnapshot.self, resource: "demo-dashboard")
    }

    public func faq() throws -> FAQResponse {
        try decode(FAQResponse.self, resource: "demo-faq")
    }

    public func chatResponse() throws -> ChatResponse {
        try decode(ChatResponse.self, resource: "demo-chat")
    }

    private func decode<T: Decodable>(_ type: T.Type, resource: String) throws -> T {
        guard let url = bundle.url(forResource: resource, withExtension: "json") else {
            throw CocoaError(.fileNoSuchFile)
        }
        return try JSONDecoder.ems.decode(type, from: Data(contentsOf: url))
    }
}
```

- [ ] **Step 7: Run tests**

Run:

```bash
cd ios/EMSControl
swift test
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 2**

Run:

```bash
git add ios/EMSControl
git commit -m "feat: add iOS core models and demo fixtures"
```

---

### Task 3: iOS API Client, Dashboard Store, and Live Dashboard View

**Files:**
- Create: `ios/EMSControl/Sources/EMSControlCore/APIClient.swift`
- Create: `ios/EMSControl/Sources/EMSControlCore/DashboardStore.swift`
- Create: `ios/EMSControl/Sources/EMSControlApp/EMSControlApp.swift`
- Create: `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`
- Create: `ios/EMSControl/Sources/EMSControlApp/ConnectionView.swift`
- Create: `ios/EMSControl/Sources/EMSControlApp/DashboardView.swift`
- Create: `ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift`
- Create: `ios/EMSControl/Tests/EMSControlCoreTests/DashboardStoreTests.swift`
- Create: `docs/ios-validation/iteration-3-dashboard-notes.md`

**Interfaces:**
- Consumes `DashboardSnapshot`, `DemoDataStore`.
- Produces `APIClient.fetchDashboard() async throws -> DashboardSnapshot`.
- Produces `DashboardStore.refresh() async`, `DashboardStore.useDemo()`, `DashboardStore.forgetServer()`.

- [ ] **Step 1: Write failing API client tests**

Create `ios/EMSControl/Tests/EMSControlCoreTests/APIClientTests.swift`:

```swift
import Foundation
import XCTest
@testable import EMSControlCore

final class APIClientTests: XCTestCase {
    func testAuthorizationHeaderUsesBearerToken() async throws {
        let transport = RecordingTransport(data: dashboardJSON(apiVersion: 1))
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, token: "abc123", transport: transport)

        _ = try await client.fetchDashboard()

        XCTAssertEqual(transport.lastRequest?.value(forHTTPHeaderField: "Authorization"), "Bearer abc123")
    }

    func testRejectsFutureDashboardAPIVersion() async {
        let client = APIClient(baseURL: URL(string: "http://ems.local:8080")!, transport: RecordingTransport(data: dashboardJSON(apiVersion: 99)))

        do {
            _ = try await client.fetchDashboard()
            XCTFail("expected incompatible server")
        } catch APIClientError.incompatibleServer(let version) {
            XCTAssertEqual(version, 99)
        } catch {
            XCTFail("unexpected error: \(error)")
        }
    }
}

private final class RecordingTransport: HTTPTransport {
    var lastRequest: URLRequest?
    let data: Data

    init(data: Data) { self.data = data }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        lastRequest = request
        return (data, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

private func dashboardJSON(apiVersion: Int) -> Data {
    """
    {
      "api_version": \(apiVersion),
      "generated_at": "2026-06-29T12:00:00+00:00",
      "server_time": "2026-06-29T12:00:00+00:00",
      "server_name": "Home EMS",
      "cache_ttl_seconds": 10,
      "degraded_sections": [],
      "readiness": {},
      "status": {},
      "freshness": {},
      "strategy": {},
      "decision": {},
      "alerts": {},
      "battery": {},
      "charge_need": {},
      "savings": {},
      "energy_story": {},
      "ai_validation": {}
    }
    """.data(using: .utf8)!
}
```

- [ ] **Step 2: Write failing dashboard store tests**

Create `ios/EMSControl/Tests/EMSControlCoreTests/DashboardStoreTests.swift`:

```swift
import XCTest
@testable import EMSControlCore

@MainActor
final class DashboardStoreTests: XCTestCase {
    func testRefreshKeepsStaleSnapshotAfterFailure() async throws {
        let good = DemoDataStore(bundle: .module)
        let store = DashboardStore(client: nil, demoData: good)
        try store.useDemo()
        let first = store.snapshot

        store.client = APIClient(baseURL: URL(string: "http://127.0.0.1:1")!, transport: FailingTransport())
        await store.refresh()

        XCTAssertEqual(store.snapshot, first)
        XCTAssertTrue(store.isStale)
    }

    func testForgetServerClearsSnapshot() throws {
        let store = DashboardStore(client: nil, demoData: DemoDataStore(bundle: .module))
        try store.useDemo()
        store.forgetServer()
        XCTAssertNil(store.snapshot)
    }
}

private struct FailingTransport: HTTPTransport {
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        throw URLError(.notConnectedToInternet)
    }
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
cd ios/EMSControl
swift test
```

Expected: fails because `APIClient`, `HTTPTransport`, and `DashboardStore` do not exist.

- [ ] **Step 4: Implement API client**

Create `ios/EMSControl/Sources/EMSControlCore/APIClient.swift`:

```swift
import Foundation

public protocol HTTPTransport {
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

public struct URLSessionTransport: HTTPTransport {
    public init() {}
    public func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIClientError.invalidResponse }
        return (data, http)
    }
}

public enum APIClientError: Error, Equatable {
    case invalidResponse
    case httpStatus(Int)
    case incompatibleServer(Int)
}

public struct APIClient {
    public let baseURL: URL
    public let token: String?
    private let transport: HTTPTransport

    public init(baseURL: URL, token: String? = nil, transport: HTTPTransport = URLSessionTransport()) {
        self.baseURL = baseURL
        self.token = token
        self.transport = transport
    }

    public func fetchDashboard() async throws -> DashboardSnapshot {
        var request = URLRequest(url: baseURL.appending(path: "api/dashboard"))
        request.httpMethod = "GET"
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await transport.data(for: request)
        guard (200..<300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        let snapshot = try JSONDecoder.ems.decode(DashboardSnapshot.self, from: data)
        guard snapshot.apiVersion <= 1 else {
            throw APIClientError.incompatibleServer(snapshot.apiVersion)
        }
        return snapshot
    }
}
```

- [ ] **Step 5: Implement dashboard store**

Create `ios/EMSControl/Sources/EMSControlCore/DashboardStore.swift`:

```swift
import Foundation
import Observation

@MainActor
@Observable
public final class DashboardStore {
    public var client: APIClient?
    public private(set) var snapshot: DashboardSnapshot?
    public private(set) var isLoading = false
    public private(set) var isStale = false
    public private(set) var lastError: String?

    private let demoData: DemoDataStore

    public init(client: APIClient?, demoData: DemoDataStore = DemoDataStore()) {
        self.client = client
        self.demoData = demoData
    }

    public func refresh() async {
        guard let client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            snapshot = try await client.fetchDashboard()
            isStale = false
            lastError = nil
        } catch {
            isStale = snapshot != nil
            lastError = String(describing: error)
        }
    }

    public func useDemo() throws {
        snapshot = try demoData.dashboardSnapshot()
        isStale = false
        lastError = nil
    }

    public func forgetServer() {
        client = nil
        snapshot = nil
        isStale = false
        lastError = nil
    }
}
```

- [ ] **Step 6: Add SwiftUI shell and dashboard view**

Create `ios/EMSControl/Sources/EMSControlApp/EMSControlApp.swift`:

```swift
import SwiftUI
import EMSControlCore

@main
struct EMSControlApp: App {
    @State private var dashboardStore = DashboardStore(client: nil)

    var body: some Scene {
        WindowGroup {
            AppShellView()
                .environment(dashboardStore)
        }
    }
}
```

Create `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`:

```swift
import SwiftUI
import EMSControlCore

struct AppShellView: View {
    @Environment(DashboardStore.self) private var dashboardStore

    var body: some View {
        TabView {
            DashboardView()
                .tabItem { Label("Dashboard", systemImage: "bolt.horizontal.circle") }
            Text("Chat")
                .tabItem { Label("Chat", systemImage: "message") }
        }
        .sheet(isPresented: .constant(dashboardStore.snapshot == nil)) {
            ConnectionView()
        }
    }
}
```

Create `ios/EMSControl/Sources/EMSControlApp/ConnectionView.swift`:

```swift
import SwiftUI
import EMSControlCore

struct ConnectionView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @State private var baseURL = "http://"

    var body: some View {
        NavigationStack {
            Form {
                Section("Connect") {
                    TextField("Server URL", text: $baseURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                    Button("Connect") {
                        if let url = URL(string: baseURL) {
                            dashboardStore.client = APIClient(baseURL: url)
                            Task { await dashboardStore.refresh() }
                        }
                    }
                    Button("View Demo") {
                        try? dashboardStore.useDemo()
                    }
                }
            }
            .navigationTitle("EMS Server")
        }
    }
}
```

Create `ios/EMSControl/Sources/EMSControlApp/DashboardView.swift`:

```swift
import SwiftUI
import EMSControlCore

struct DashboardView: View {
    @Environment(DashboardStore.self) private var dashboardStore

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let snapshot = dashboardStore.snapshot {
                        HStack {
                            VStack(alignment: .leading) {
                                Text(snapshot.serverName)
                                    .font(.headline)
                                Text(snapshot.isDemo ? "Demo data" : (dashboardStore.isStale ? "Stale" : "Live"))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if snapshot.isDemo {
                                Text("Demo")
                                    .font(.caption.bold())
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 5)
                                    .background(.thinMaterial)
                                    .clipShape(Capsule())
                            }
                        }
                        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                            DashboardCard(title: "Battery", value: snapshot.status.values["soc_pct"]?.displayValue ?? "--")
                            DashboardCard(title: "Mode", value: snapshot.decision.values["intent"]?.displayValue ?? "--")
                            DashboardCard(title: "Savings", value: snapshot.savings.values["today_eur"]?.displayValue ?? "--")
                            DashboardCard(title: "Plan", value: snapshot.energyStory.values["headline"]?.displayValue ?? "Open story")
                        }
                    }
                }
                .padding()
            }
            .navigationTitle("Dashboard")
            .refreshable { await dashboardStore.refresh() }
        }
    }
}

private struct DashboardCard: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.headline).lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

private extension JSONValue {
    var displayValue: String {
        switch self {
        case .string(let value): value
        case .number(let value): value.formatted()
        case .bool(let value): value ? "Yes" : "No"
        case .object, .array: "Details"
        case .null: "--"
        }
    }
}
```

- [ ] **Step 7: Run Swift tests**

Run:

```bash
cd ios/EMSControl
swift test
```

Expected: all Swift package tests pass.

- [ ] **Step 8: Record validation note**

Create `docs/ios-validation/iteration-3-dashboard-notes.md`:

```markdown
# Iteration 3 Dashboard Validation

- Command: `cd ios/EMSControl && swift test`
- Result: passing after `DashboardStore`, `APIClient`, and dashboard shell were added.
- Visual note: package-level SwiftUI source exists; simulator screenshots are captured after the Xcode app target is added in Task 5.
```

- [ ] **Step 9: Commit Task 3**

Run:

```bash
git add ios/EMSControl docs/ios-validation/iteration-3-dashboard-notes.md
git commit -m "feat: add iOS dashboard client foundation"
```

---

### Task 4: iOS Chat Store and Chat View

**Files:**
- Modify: `ios/EMSControl/Sources/EMSControlCore/APIClient.swift`
- Create: `ios/EMSControl/Sources/EMSControlCore/ChatStore.swift`
- Modify: `ios/EMSControl/Sources/EMSControlApp/AppShellView.swift`
- Create: `ios/EMSControl/Sources/EMSControlApp/ChatView.swift`
- Create: `ios/EMSControl/Tests/EMSControlCoreTests/ChatStoreTests.swift`
- Create: `docs/ios-validation/iteration-4-chat-notes.md`

**Interfaces:**
- Consumes `GET /api/explainer`, `GET /api/faq`, `POST /api/chat`.
- Produces `ChatStore.loadFAQ()`, `ChatStore.send(question:)`, `ChatStore.clearSession()`.

- [ ] **Step 1: Write failing chat store tests**

Create `ios/EMSControl/Tests/EMSControlCoreTests/ChatStoreTests.swift`:

```swift
import XCTest
@testable import EMSControlCore

@MainActor
final class ChatStoreTests: XCTestCase {
    func testEmptyQuestionIsIgnored() async {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        await store.send(question: "   ")
        XCTAssertTrue(store.messages.isEmpty)
    }

    func testDemoChatAddsQuestionAndAnswer() async throws {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        try store.useDemoFAQ()
        await store.send(question: "What is the plan?")
        XCTAssertEqual(store.messages.count, 2)
        XCTAssertEqual(store.messages[0].role, .user)
        XCTAssertEqual(store.messages[1].role, .assistant)
    }

    func testClearSessionRemovesMessagesAndFAQ() throws {
        let store = ChatStore(client: nil, demoData: DemoDataStore(bundle: .module))
        try store.useDemoFAQ()
        store.clearSession()
        XCTAssertTrue(store.messages.isEmpty)
        XCTAssertTrue(store.faqItems.isEmpty)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd ios/EMSControl
swift test
```

Expected: fails because `ChatStore` does not exist and `APIClient` has no chat methods.

- [ ] **Step 3: Add chat methods to API client**

Append to `APIClient`:

```swift
    public func fetchExplainer() async throws -> ExplainerStatus {
        try await get("api/explainer", as: ExplainerStatus.self)
    }

    public func fetchFAQ() async throws -> FAQResponse {
        try await get("api/faq", as: FAQResponse.self)
    }

    public func sendChat(question: String) async throws -> ChatResponse {
        var request = URLRequest(url: baseURL.appending(path: "api/chat"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONEncoder.ems.encode(ChatRequest(question: question))
        let (data, response) = try await transport.data(for: request)
        guard (200..<300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder.ems.decode(ChatResponse.self, from: data)
    }

    private func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = "GET"
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, response) = try await transport.data(for: request)
        guard (200..<300).contains(response.statusCode) else {
            throw APIClientError.httpStatus(response.statusCode)
        }
        return try JSONDecoder.ems.decode(type, from: data)
    }
```

- [ ] **Step 4: Implement chat store**

Create `ios/EMSControl/Sources/EMSControlCore/ChatStore.swift`:

```swift
import Foundation
import Observation

public enum ChatRole: Equatable {
    case user
    case assistant
}

public struct ChatMessage: Equatable, Identifiable {
    public let id = UUID()
    public let role: ChatRole
    public let text: String
}

@MainActor
@Observable
public final class ChatStore {
    public var client: APIClient?
    public private(set) var messages: [ChatMessage] = []
    public private(set) var faqItems: [FAQItem] = []
    public private(set) var isBusy = false
    public private(set) var lastError: String?

    private let demoData: DemoDataStore

    public init(client: APIClient?, demoData: DemoDataStore = DemoDataStore()) {
        self.client = client
        self.demoData = demoData
    }

    public func loadFAQ() async {
        do {
            if let client {
                faqItems = try await client.fetchFAQ().items
            } else {
                try useDemoFAQ()
            }
            lastError = nil
        } catch {
            lastError = String(describing: error)
        }
    }

    public func useDemoFAQ() throws {
        faqItems = try demoData.faq().items
    }

    public func send(question: String) async {
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isBusy else { return }
        messages.append(ChatMessage(role: .user, text: trimmed))
        isBusy = true
        defer { isBusy = false }
        do {
            let response = try await (client?.sendChat(question: trimmed) ?? demoData.chatResponse())
            messages.append(ChatMessage(role: .assistant, text: response.answer))
            lastError = nil
        } catch {
            lastError = String(describing: error)
        }
    }

    public func clearSession() {
        messages.removeAll()
        faqItems.removeAll()
        lastError = nil
        isBusy = false
    }
}
```

- [ ] **Step 5: Add Chat view**

Modify `AppShellView.swift` so the Chat tab uses `ChatView()`:

```swift
ChatView()
    .tabItem { Label("Chat", systemImage: "message") }
```

Create `ios/EMSControl/Sources/EMSControlApp/ChatView.swift`:

```swift
import SwiftUI
import EMSControlCore

struct ChatView: View {
    @State private var store = ChatStore(client: nil)
    @State private var input = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                List {
                    if !store.faqItems.isEmpty {
                        Section("Quick answers") {
                            ForEach(store.faqItems) { item in
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(item.question).font(.subheadline.bold())
                                    Text(item.answer).font(.subheadline).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                    Section("Messages") {
                        ForEach(store.messages) { message in
                            Text(message.text)
                                .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
                        }
                    }
                }
                HStack {
                    TextField("Ask a question", text: $input)
                        .textFieldStyle(.roundedBorder)
                    Button {
                        let question = input
                        input = ""
                        Task { await store.send(question: question) }
                    } label: {
                        Image(systemName: "paperplane.fill")
                    }
                    .disabled(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.isBusy)
                }
                .padding()
            }
            .navigationTitle("Chat")
            .task { await store.loadFAQ() }
        }
    }
}
```

- [ ] **Step 6: Run Swift tests**

Run:

```bash
cd ios/EMSControl
swift test
```

Expected: all Swift tests pass.

- [ ] **Step 7: Record validation note**

Create `docs/ios-validation/iteration-4-chat-notes.md`:

```markdown
# Iteration 4 Chat Validation

- Command: `cd ios/EMSControl && swift test`
- Result: passing after `ChatStore`, chat API methods, and `ChatView` were added.
- Privacy validation: chat messages are memory-only in `ChatStore` and `clearSession()` removes all message/FAQ state.
```

- [ ] **Step 8: Commit Task 4**

Run:

```bash
git add ios/EMSControl docs/ios-validation/iteration-4-chat-notes.md
git commit -m "feat: add iOS grounded chat flow"
```

---

### Task 5: App Store Polish, Xcode App Target, Validation Evidence

**Files:**
- Create or modify: `ios/EMSControl/EMSControl.xcodeproj/project.pbxproj`
- Create: `ios/EMSControl/Sources/EMSControlApp/Info.plist`
- Create: `ios/EMSControl/Sources/EMSControlApp/Assets.xcassets/AppIcon.appiconset/Contents.json`
- Create: `ios/EMSControl/Sources/EMSControlApp/Assets.xcassets/AccentColor.colorset/Contents.json`
- Create: `ios/EMSControl/Sources/EMSControlCore/ServerDiscovery.swift`
- Create: `ios/EMSControl/Tests/EMSControlCoreTests/ServerDiscoveryTests.swift`
- Create: `ios/EMSControl/README.md`
- Create: `docs/ios-validation/iteration-5-app-store-polish.md`

**Interfaces:**
- Produces `ServerDiscovery.parsePairingPayload(_:) -> PairingPayload`.
- Produces an Xcode app target with bundle id `com.jeroenniesen.emscontrol`, Local Network usage copy, camera permission only if QR scanning is actually wired, and ATS local-network HTTP allowance.

- [ ] **Step 1: Write failing discovery tests**

Create `ios/EMSControl/Tests/EMSControlCoreTests/ServerDiscoveryTests.swift`:

```swift
import XCTest
@testable import EMSControlCore

final class ServerDiscoveryTests: XCTestCase {
    func testManualURLNormalizesTrailingSlash() throws {
        let discovery = ServerDiscovery()
        let url = try discovery.normalizedManualURL("http://ems.local:8080/")
        XCTAssertEqual(url.absoluteString, "http://ems.local:8080")
    }

    func testQRPayloadParsesURLAndLabelWithoutToken() throws {
        let payload = try ServerDiscovery().parsePairingPayload(#"{"base_url":"http://ems.local:8080","server_label":"Home EMS"}"#)
        XCTAssertEqual(payload.baseURL.absoluteString, "http://ems.local:8080")
        XCTAssertEqual(payload.serverLabel, "Home EMS")
    }

    func testQRPayloadRejectsEmbeddedToken() {
        XCTAssertThrowsError(try ServerDiscovery().parsePairingPayload(#"{"base_url":"http://ems.local:8080","token":"secret"}"#))
    }
}
```

- [ ] **Step 2: Implement discovery parsing**

Create `ios/EMSControl/Sources/EMSControlCore/ServerDiscovery.swift`:

```swift
import Foundation

public struct PairingPayload: Equatable {
    public let baseURL: URL
    public let serverLabel: String?
}

public enum ServerDiscoveryError: Error, Equatable {
    case invalidURL
    case tokenNotAllowed
}

public struct ServerDiscovery {
    public init() {}

    public func normalizedManualURL(_ input: String) throws -> URL {
        let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var components = URLComponents(string: trimmed),
              components.scheme == "http" || components.scheme == "https",
              components.host != nil
        else {
            throw ServerDiscoveryError.invalidURL
        }
        components.path = components.path == "/" ? "" : components.path
        guard let url = components.url else { throw ServerDiscoveryError.invalidURL }
        return url
    }

    public func parsePairingPayload(_ raw: String) throws -> PairingPayload {
        struct RawPayload: Decodable {
            let baseURL: String
            let serverLabel: String?
            let token: String?
        }
        let data = Data(raw.utf8)
        let payload = try JSONDecoder.ems.decode(RawPayload.self, from: data)
        if payload.token != nil { throw ServerDiscoveryError.tokenNotAllowed }
        return PairingPayload(
            baseURL: try normalizedManualURL(payload.baseURL),
            serverLabel: payload.serverLabel
        )
    }
}
```

- [ ] **Step 3: Add app metadata**

Create `ios/EMSControl/Sources/EMSControlApp/Info.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>EMS Control</string>
  <key>NSLocalNetworkUsageDescription</key>
  <string>EMS Control searches your local network only to find your own Energy Management System server.</string>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
  </dict>
</dict>
</plist>
```

Create `ios/EMSControl/Sources/EMSControlApp/Assets.xcassets/AccentColor.colorset/Contents.json`:

```json
{
  "colors": [
    {
      "idiom": "universal",
      "color": {
        "color-space": "srgb",
        "components": {
          "red": "0x46",
          "green": "0xC8",
          "blue": "0xA8",
          "alpha": "1.000"
        }
      }
    }
  ],
  "info": {
    "author": "xcode",
    "version": 1
  }
}
```

Create `ios/EMSControl/Sources/EMSControlApp/Assets.xcassets/AppIcon.appiconset/Contents.json`:

```json
{
  "images": [
    {
      "idiom": "universal",
      "platform": "ios",
      "size": "1024x1024"
    }
  ],
  "info": {
    "author": "xcode",
    "version": 1
  }
}
```

- [ ] **Step 4: Add Xcode app target**

Use Xcode or a checked-in minimal project file to create `ios/EMSControl/EMSControl.xcodeproj` with:

```text
Project: EMSControl
Target: EMSControl
Bundle identifier: com.jeroenniesen.emscontrol
Deployment target: iOS 17.0
Sources:
  Sources/EMSControlApp/*.swift
  Sources/EMSControlCore/*.swift
Resources:
  Resources/*.json
  Sources/EMSControlApp/Info.plist
  Sources/EMSControlApp/Assets.xcassets
```

Do not include camera permission unless QR scanning UI is implemented in this task. If QR scanning is only payload parsing, no camera permission is needed.

- [ ] **Step 5: Add README and reviewer notes**

Create `ios/EMSControl/README.md`:

```markdown
# EMS Control iOS

Native SwiftUI iOS app for the Energy Management System.

## First Launch

- Enter a LAN/VPN EMS URL such as `http://192.168.1.20:8080`.
- Or choose Demo mode to inspect the app without a server.
- QR pairing payloads use JSON: `{"base_url":"http://ems.local:8080","server_label":"Home EMS"}`.
- Tokens are entered manually and are never embedded in QR payloads.

## App Store Review

Use Demo mode from first launch. It shows synthetic data and does not require a private EMS server.

## Local Validation

Run unit tests:

```bash
cd ios/EMSControl
swift test
```

Build app target after `EMSControl.xcodeproj` exists:

```bash
xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' build
```
```

- [ ] **Step 6: Run tests and build**

Run:

```bash
cd ios/EMSControl
swift test
```

Expected: all Swift package tests pass.

Run:

```bash
xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination 'platform=iOS Simulator,name=iPhone 17,OS=26.5' build
```

Expected: app target builds. If the named simulator is unavailable, run `xcrun simctl list devices available` and use an available iPhone simulator. Record the exact destination in validation notes.

- [ ] **Step 7: Capture validation evidence**

Create `docs/ios-validation/iteration-5-app-store-polish.md`:

```markdown
# Iteration 5 App Store Polish Validation

- Swift tests: `cd ios/EMSControl && swift test`
- Xcode build: `xcodebuild -project ios/EMSControl/EMSControl.xcodeproj -scheme EMSControl -destination '<actual destination>' build`
- Demo mode: available from first launch and visibly labeled.
- Permissions: Local Network usage text present; camera permission omitted unless QR camera UI is implemented.
- Privacy: no analytics SDK, no iCloud sync of server data, session-only chat.
- Liquid Glass: navigation/sheets may use material; telemetry cards remain high-contrast.
- Screenshots captured:
  - `docs/ios-validation/iteration-5-iphone-dashboard-dark.png`
  - `docs/ios-validation/iteration-5-iphone-dashboard-light.png`
  - `docs/ios-validation/iteration-5-iphone-chat-dark.png`
  - `docs/ios-validation/iteration-5-iphone-demo.png`
```

- [ ] **Step 8: Commit Task 5**

Run:

```bash
git add ios/EMSControl docs/ios-validation
git commit -m "feat: polish iOS app for App Store review"
```

---

## Plan Self-Review

Spec coverage:

- Dashboard/chat first iteration: Tasks 1, 3, and 4.
- Server scan/manual IP/VPN: Task 5 discovery and README; manual URL appears in Task 3 connection view.
- Caching/no doubled calls: Task 1 snapshot cache tests and API contract.
- Same color scheme: Task 2 theme tests.
- App Store readiness: Task 5 Demo mode, Info.plist, README, validation evidence.
- Liquid Glass/Apple HIG: Task 5 validation note and Task 3 view constraints; detailed visual polish continues during app execution.
- Maintainability: file structure splits API client, stores, theme, discovery, demo data, and views.
- Five loops: Tasks 1 through 5 map directly to the approved five iteration loop.

Placeholder scan:

- The plan uses concrete file paths, commands, target names, test snippets, and expected outcomes.
- Task 5's Xcode project creation is concrete in target/source/resource terms because the `.pbxproj` content is generated by Xcode or by a checked-in minimal project file during execution; all required target settings are specified.

Type consistency:

- `DashboardSnapshot.apiVersion`, `cacheTTLSeconds`, and `degradedSections` match the JSON contract under Swift `.convertFromSnakeCase`.
- `DemoDataStore.dashboardSnapshot()`, `faq()`, and `chatResponse()` are used by their tests and stores.
- `APIClient.fetchDashboard()`, `fetchFAQ()`, `fetchExplainer()`, and `sendChat(question:)` are used by stores.
- `ChatStore.clearSession()` is the method referenced by privacy tests and forget-server flow.
