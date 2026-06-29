import SwiftUI
import EMSControlCore

struct ConnectionView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme
    @State private var baseURL = "http://"
    @State private var validationError: String?

    private var theme: EMSTheme {
        colorScheme == .dark ? .dark : .light
    }

    private var errorMessage: String? {
        validationError ?? dashboardStore.lastError
    }

    var body: some View {
        NavigationStack {
            ZStack {
                themeColor(theme.background)
                    .ignoresSafeArea()

                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("EMS Server")
                                .font(.title2.weight(.semibold))
                                .foregroundStyle(themeColor(theme.text))
                            Text("Connect to a live server or open the packaged demo.")
                                .foregroundStyle(themeColor(theme.muted))
                        }

                        VStack(alignment: .leading, spacing: 12) {
                            Text("Server URL")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(themeColor(theme.muted))
                            TextField("http://ems.local:8080", text: $baseURL)
                                .textInputAutocapitalization(.never)
                                .keyboardType(.URL)
                                .autocorrectionDisabled()
                                .padding(14)
                                .background(themeColor(theme.secondaryPanel))
                                .foregroundStyle(themeColor(theme.text))
                                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                                .overlay {
                                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                                        .stroke(themeColor(validationError == nil ? theme.line : theme.error), lineWidth: 1)
                                }
                                .onChange(of: baseURL) { _, _ in
                                    validationError = nil
                                }
                        }
                        .padding(20)
                        .background(themeColor(theme.panel))
                        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .overlay {
                            RoundedRectangle(cornerRadius: 16, style: .continuous)
                                .stroke(themeColor(theme.line), lineWidth: 1)
                        }

                        VStack(spacing: 12) {
                            Button("Connect") {
                                do {
                                    let url = try ServerAddressValidator.validatedBaseURL(baseURL)
                                    validationError = nil
                                    dashboardStore.client = APIClient(baseURL: url)
                                    Task { await dashboardStore.refresh() }
                                } catch {
                                    validationError = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
                                }
                            }
                            .buttonStyle(PrimaryEMSButtonStyle(theme: theme))

                            Button("View Demo") {
                                dashboardStore.loadDemo()
                            }
                            .buttonStyle(SecondaryEMSButtonStyle(theme: theme))
                        }

                        if let error = errorMessage {
                            Text(error)
                                .font(.footnote)
                                .foregroundStyle(themeColor(theme.error))
                                .padding(16)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(themeColor(theme.panel))
                                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                                .overlay {
                                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                                        .stroke(themeColor(theme.error).opacity(0.35), lineWidth: 1)
                                }
                        }
                    }
                    .padding(20)
                }
            }
            .navigationTitle("EMS Server")
        }
    }
}

private struct PrimaryEMSButtonStyle: ButtonStyle {
    let theme: EMSTheme

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(themeColor(theme.accent).opacity(configuration.isPressed ? 0.85 : 1))
            .foregroundStyle(themeColor(theme.background))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

private struct SecondaryEMSButtonStyle: ButtonStyle {
    let theme: EMSTheme

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(themeColor(theme.panel).opacity(configuration.isPressed ? 0.88 : 1))
            .foregroundStyle(themeColor(theme.text))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(themeColor(theme.line), lineWidth: 1)
            }
    }
}
