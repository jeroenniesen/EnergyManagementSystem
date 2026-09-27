import SwiftUI
import EMSControlCore

/// B-09 / #129 — three-line unreachable banner (parity with web `EMS_UNREACHABLE` + `Laatst bekend`).
/// Shared by Dashboard (stale) and ConnectionView (cold fail after a restored client).
struct UnreachableFailureBanner: View {
    let lastUpdatedAt: Date?
    let theme: EMSTheme

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(EMSUnreachableCopy.message)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(themeColor(theme.error))
                .fixedSize(horizontal: false, vertical: true)

            Text(EMSUnreachableCopy.emsDoing)
                .font(.caption)
                .foregroundStyle(themeColor(theme.muted))
                .fixedSize(horizontal: false, vertical: true)

            Text(formatLaatstBekend(lastUpdatedAt))
                .font(.caption.weight(.semibold))
                .foregroundStyle(themeColor(theme.text))
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(themeColor(theme.panel))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(themeColor(theme.error).opacity(0.55), lineWidth: 1)
        }
        .accessibilityElement(children: .combine)
        // Join with a single space — message/emsDoing already end with "." (avoid "device.. In").
        .accessibilityLabel(
            "\(EMSUnreachableCopy.message) \(EMSUnreachableCopy.emsDoing) \(formatLaatstBekend(lastUpdatedAt))"
        )
    }
}
