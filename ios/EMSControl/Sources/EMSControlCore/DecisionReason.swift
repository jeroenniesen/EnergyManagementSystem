import Foundation

// B-74 / #84 slice 3: structured `/api/battery-plan` `reason` object — same contract as web
// `decisionWhy.DecisionReason` / `DecisionReasonDetails` (slice 2). No parallel schema.

/// Chosen planner window (typically the cheap charge block).
public struct DecisionChosenWindow: Codable, Equatable, Sendable {
    public let start: String?
    public let end: String?
    public let intent: String?
    public let label: String?
    public let eurPerKwhMin: Double?
    public let eurPerKwhMax: Double?

    public init(
        start: String? = nil,
        end: String? = nil,
        intent: String? = nil,
        label: String? = nil,
        eurPerKwhMin: Double? = nil,
        eurPerKwhMax: Double? = nil
    ) {
        self.start = start
        self.end = end
        self.intent = intent
        self.label = label
        self.eurPerKwhMin = eurPerKwhMin
        self.eurPerKwhMax = eurPerKwhMax
    }
}

/// Alternative the planner considered and rejected.
public struct DecisionRejectedAlternative: Codable, Equatable, Sendable {
    public let intent: String?
    public let reason: String
    public let windowStart: String?
    public let windowEnd: String?

    public init(
        intent: String? = nil,
        reason: String,
        windowStart: String? = nil,
        windowEnd: String? = nil
    ) {
        self.intent = intent
        self.reason = reason
        self.windowStart = windowStart
        self.windowEnd = windowEnd
    }
}

public struct DecisionExpectedBenefit: Codable, Equatable, Sendable {
    public let eur: Double?
    public let summary: String

    public init(eur: Double? = nil, summary: String) {
        self.eur = eur
        self.summary = summary
    }
}

public struct DecisionRisk: Codable, Equatable, Sendable {
    public let marginEurPerKwh: Double?
    public let summary: String

    public init(marginEurPerKwh: Double? = nil, summary: String) {
        self.marginEurPerKwh = marginEurPerKwh
        self.summary = summary
    }
}

public struct DecisionSafetyConstraint: Codable, Equatable, Sendable {
    public let code: String?
    public let message: String?
    /// `"paused"` | `"proceed"` (and any future gate vocabulary).
    public let action: String

    public init(code: String? = nil, message: String? = nil, action: String = "proceed") {
        self.code = code
        self.message = message
        self.action = action
    }
}

/// Factual control-gate outcomes for this decision cycle.
public struct DecisionGateOutcomes: Codable, Equatable, Sendable {
    public let validatorCode: String?
    public let failsafe: Bool
    public let dwell: Bool
    public let capReached: Bool
    public let unconfirmed: Bool

    public init(
        validatorCode: String? = nil,
        failsafe: Bool = false,
        dwell: Bool = false,
        capReached: Bool = false,
        unconfirmed: Bool = false
    ) {
        self.validatorCode = validatorCode
        self.failsafe = failsafe
        self.dwell = dwell
        self.capReached = capReached
        self.unconfirmed = unconfirmed
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        validatorCode = try c.decodeIfPresent(String.self, forKey: .validatorCode)
        failsafe = try c.decodeIfPresent(Bool.self, forKey: .failsafe) ?? false
        dwell = try c.decodeIfPresent(Bool.self, forKey: .dwell) ?? false
        capReached = try c.decodeIfPresent(Bool.self, forKey: .capReached) ?? false
        unconfirmed = try c.decodeIfPresent(Bool.self, forKey: .unconfirmed) ?? false
    }
}

/// Cross-surface structured reason for one optimization / control decision (B-74 / #84).
public struct DecisionReasonSnapshot: Codable, Equatable, Sendable {
    public let chosenWindow: DecisionChosenWindow?
    public let rejectedAlternative: DecisionRejectedAlternative?
    public let expectedBenefit: DecisionExpectedBenefit?
    public let risk: DecisionRisk?
    public let safetyConstraint: DecisionSafetyConstraint
    public let gates: DecisionGateOutcomes
    public let summary: String

    public init(
        chosenWindow: DecisionChosenWindow? = nil,
        rejectedAlternative: DecisionRejectedAlternative? = nil,
        expectedBenefit: DecisionExpectedBenefit? = nil,
        risk: DecisionRisk? = nil,
        safetyConstraint: DecisionSafetyConstraint = DecisionSafetyConstraint(action: "proceed"),
        gates: DecisionGateOutcomes = DecisionGateOutcomes(),
        summary: String = ""
    ) {
        self.chosenWindow = chosenWindow
        self.rejectedAlternative = rejectedAlternative
        self.expectedBenefit = expectedBenefit
        self.risk = risk
        self.safetyConstraint = safetyConstraint
        self.gates = gates
        self.summary = summary
    }

    /// Tolerant decode: older/partial payloads still yield a usable reason object.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        chosenWindow = try c.decodeIfPresent(DecisionChosenWindow.self, forKey: .chosenWindow)
        rejectedAlternative = try c.decodeIfPresent(
            DecisionRejectedAlternative.self, forKey: .rejectedAlternative)
        expectedBenefit = try c.decodeIfPresent(DecisionExpectedBenefit.self, forKey: .expectedBenefit)
        risk = try c.decodeIfPresent(DecisionRisk.self, forKey: .risk)
        safetyConstraint = try c.decodeIfPresent(
            DecisionSafetyConstraint.self, forKey: .safetyConstraint)
            ?? DecisionSafetyConstraint(action: "proceed")
        gates = try c.decodeIfPresent(DecisionGateOutcomes.self, forKey: .gates)
            ?? DecisionGateOutcomes()
        summary = try c.decodeIfPresent(String.self, forKey: .summary) ?? ""
    }

    /// Demo / fixture reason matching web DecisionReasonDetails test facts.
    public static let demoCharge = DecisionReasonSnapshot(
        chosenWindow: DecisionChosenWindow(
            start: "2026-09-30T01:00:00+00:00",
            end: "2026-09-30T04:00:00+00:00",
            intent: "grid_charge_to_target",
            label: "cheap charge window",
            eurPerKwhMin: 0.05,
            eurPerKwhMax: 0.08
        ),
        rejectedAlternative: DecisionRejectedAlternative(
            intent: "allow_self_consumption",
            reason: "self-consumption only — rejected in favour of the selected charge window"
        ),
        expectedBenefit: DecisionExpectedBenefit(
            eur: 0.85,
            summary: "Estimated net benefit ≈ €0.85."
        ),
        risk: DecisionRisk(
            marginEurPerKwh: 0.02,
            summary: "Risk margin €0.020/kWh applied to break-even."
        ),
        safetyConstraint: DecisionSafetyConstraint(action: "proceed"),
        gates: DecisionGateOutcomes(),
        summary: "Charging in the cheap night window."
    )
}

/// One display row for the structured reason panel (web `DecisionReasonDetails` parity).
public struct DecisionReasonRow: Equatable, Sendable, Identifiable {
    public var id: String { title }
    public let title: String
    public let detail: String

    public init(title: String, detail: String) {
        self.title = title
        self.detail = detail
    }
}

/// Pure formatters — same wording as web `DecisionReasonDetails.tsx` so iOS tells one story.
public enum DecisionReasonFormatting {
    /// Fact rows from the reason object. Empty when `reason` is nil.
    public static func detailRows(from reason: DecisionReasonSnapshot?) -> [DecisionReasonRow] {
        guard let reason else { return [] }
        var rows: [DecisionReasonRow] = []

        let summary = reason.summary.trimmingCharacters(in: .whitespacesAndNewlines)
        if !summary.isEmpty {
            rows.append(DecisionReasonRow(title: "Summary", detail: summary))
        }

        if let chosen = reason.chosenWindow {
            rows.append(DecisionReasonRow(title: "Chosen window", detail: chosenWindowText(chosen)))
        }

        if let alt = reason.rejectedAlternative {
            let text = alt.reason.trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty {
                rows.append(DecisionReasonRow(title: "Rejected alternative", detail: text))
            }
        }

        if let benefit = reason.expectedBenefit {
            rows.append(DecisionReasonRow(title: "Expected benefit", detail: benefitText(benefit)))
        }

        if let risk = reason.risk {
            let text = risk.summary.trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty {
                rows.append(DecisionReasonRow(title: "Risk", detail: text))
            }
        }

        rows.append(DecisionReasonRow(
            title: "Safety",
            detail: safetyLabel(safety: reason.safetyConstraint, gates: reason.gates)
        ))

        let chips = gateChips(reason.gates)
        if !chips.isEmpty {
            rows.append(DecisionReasonRow(title: "Gates", detail: chips.joined(separator: " · ")))
        }

        return rows
    }

    public static func gateChips(_ gates: DecisionGateOutcomes) -> [String] {
        var chips: [String] = []
        if let code = gates.validatorCode, !code.isEmpty {
            chips.append("validator: \(code)")
        }
        if gates.failsafe { chips.append("failsafe") }
        if gates.dwell { chips.append("dwell") }
        if gates.capReached { chips.append("cap reached") }
        if gates.unconfirmed { chips.append("unconfirmed") }
        return chips
    }

    public static func safetyLabel(
        safety: DecisionSafetyConstraint,
        gates: DecisionGateOutcomes
    ) -> String {
        if safety.action == "paused" {
            if let message = safety.message, !message.isEmpty {
                if let code = safety.code, !code.isEmpty {
                    return "\(message) (\(code))"
                }
                return message
            }
            return safety.code ?? "Paused safely"
        }
        var holds: [String] = []
        if gates.dwell { holds.append("dwell") }
        if gates.capReached { holds.append("daily cap") }
        if gates.unconfirmed { holds.append("unconfirmed write") }
        if !holds.isEmpty {
            return "Proceed — held by \(holds.joined(separator: ", "))"
        }
        return "Proceed — no safety hold"
    }

    public static func chosenWindowText(_ chosen: DecisionChosenWindow) -> String {
        let label = chosen.label?.trimmingCharacters(in: .whitespacesAndNewlines)
        let intent = chosen.intent?.trimmingCharacters(in: .whitespacesAndNewlines)
        var parts: [String] = []
        if let label, !label.isEmpty {
            parts.append(label)
        } else if let intent, !intent.isEmpty {
            parts.append(intent)
        } else {
            parts.append("—")
        }
        if let clock = clockRange(start: chosen.start, end: chosen.end) {
            parts.append(clock)
        }
        if let min = chosen.eurPerKwhMin, min.isFinite {
            parts.append("from \(formatEur(min))/kWh")
        }
        return parts.joined(separator: " · ")
    }

    public static func benefitText(_ benefit: DecisionExpectedBenefit) -> String {
        if let eur = benefit.eur, eur.isFinite {
            let summary = benefit.summary.trimmingCharacters(in: .whitespacesAndNewlines)
            if summary.isEmpty {
                return "≈ \(formatEur(eur))"
            }
            return "≈ \(formatEur(eur)) — \(summary)"
        }
        return benefit.summary
    }

    /// Match web `eur()` — real minus for negatives, two fraction digits.
    public static func formatEur(_ value: Double) -> String {
        let sign = value < 0 ? "−" : ""
        return "\(sign)€\(String(format: "%.2f", abs(value)))"
    }

    public static func clockRange(start: String?, end: String?) -> String? {
        let startTrim = start?.trimmingCharacters(in: .whitespacesAndNewlines)
        let endTrim = end?.trimmingCharacters(in: .whitespacesAndNewlines)
        let hasStart = !(startTrim ?? "").isEmpty
        let hasEnd = !(endTrim ?? "").isEmpty
        if !hasStart && !hasEnd { return nil }

        func fmt(_ iso: String) -> String {
            guard let date = ISOTimestamp.parse(iso) else { return iso }
            let cal = Calendar.current
            let hour = cal.component(.hour, from: date)
            let minute = cal.component(.minute, from: date)
            return String(format: "%02d:%02d", hour, minute)
        }

        if let startTrim, hasStart, let endTrim, hasEnd {
            return "\(fmt(startTrim))–\(fmt(endTrim))"
        }
        if let startTrim, hasStart { return fmt(startTrim) }
        if let endTrim, hasEnd { return fmt(endTrim) }
        return nil
    }

    /// Short homeowner action label for the "Now:" chip (web BatteryActionWhy parity).
    /// `discharge` (discharge_for_load → vendor AUTO) is self-consumption, not a forced dump;
    /// `full_speed_discharge` is true forced discharge.
    public static func actionLabel(_ action: String?) -> String {
        guard let action, !action.isEmpty else { return "plan" }
        switch action {
        case "grid_charge", "charge": return "Grid charge"
        case "hold": return "Hold"
        case "discharge", "self_consumption", "self_consume": return "Self-consumption"
        case "full_speed_discharge": return "Full-speed discharge"
        case "solar_charge": return "Solar charge"
        case "paused": return "Paused"
        default: return action.replacingOccurrences(of: "_", with: " ")
        }
    }
}
