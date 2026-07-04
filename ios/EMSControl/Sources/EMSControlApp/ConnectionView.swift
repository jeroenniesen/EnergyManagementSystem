import SwiftUI
import EMSControlCore

struct ConnectionView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme
    @State private var baseURL = "http://"
    @State private var accessToken = ""
    @State private var tokenRequired = false
    @State private var isConnecting = false
    @State private var validationError: String?

    private let discovery = ServerDiscovery()
    private let credentialStore = KeychainCredentialStore()

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
                                    tokenRequired = false
                                }

                            if tokenRequired {
                                SecureField("Access token", text: $accessToken)
                                    .textInputAutocapitalization(.never)
                                    .autocorrectionDisabled()
                                    .padding(14)
                                    .background(themeColor(theme.secondaryPanel))
                                    .foregroundStyle(themeColor(theme.text))
                                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                                    .overlay {
                                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                                            .stroke(themeColor(theme.line), lineWidth: 1)
                                    }
                            }

                            if showsHTTPTokenWarning {
                                Text("Local HTTP can expose this token to devices on the same trusted LAN or VPN.")
                                    .font(.footnote)
                                    .foregroundStyle(themeColor(theme.amber))
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
                            Button(isConnecting ? "Connecting..." : "Connect") {
                                Task { await connect() }
                            }
                            .buttonStyle(PrimaryEMSButtonStyle(theme: theme))
                            .disabled(isConnecting)

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

    private var trimmedToken: String {
        accessToken.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var showsHTTPTokenWarning: Bool {
        baseURL.lowercased().trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("http://") &&
        !trimmedToken.isEmpty
    }

    private func connect() async {
        isConnecting = true
        defer { isConnecting = false }

        do {
            let url = try discovery.normalizedManualURL(baseURL)
            let savedToken = (try? credentialStore.token(for: url)) ?? ""
            let token = trimmedToken.nilIfEmpty ?? savedToken
            let client = APIClient(baseURL: url, token: token.nilIfEmpty)

            _ = try await client.fetchLiveHealth()
            _ = try await client.fetchReadyHealth()
            let auth = try await client.fetchAuthStatus()

            if auth.required && token.isEmpty {
                tokenRequired = true
                validationError = "This EMS requires an access token."
                return
            }
            if auth.required && !auth.authenticated {
                tokenRequired = true
                validationError = "Access token rejected."
                return
            }
            validationError = nil
            dashboardStore.client = client
            try? dashboardStore.saveConnectedServer(client)
            await dashboardStore.refresh()
        } catch {
            validationError = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
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
