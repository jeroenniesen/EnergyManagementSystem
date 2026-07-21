import SwiftUI
import UIKit
import EMSControlCore

struct ConnectionView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme
    @State private var baseURL = "http://"
    @State private var username = ""
    @State private var password = ""
    @State private var accessToken = ""
    @State private var useTokenLogin = false
    @State private var isConnecting = false
    @State private var validationError: String?
    @State private var didPrefill = false

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
                            Text("Sign in to a live server or open the packaged demo.")
                                .foregroundStyle(themeColor(theme.muted))
                        }

                        if dashboardStore.authFailed {
                            Text("Your session expired. Please sign in again.")
                                .font(.footnote)
                                .foregroundStyle(themeColor(theme.amber))
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }

                        VStack(alignment: .leading, spacing: 12) {
                            fieldLabel("Server URL")
                            TextField("http://ems.local:8080", text: $baseURL)
                                .textInputAutocapitalization(.never)
                                .keyboardType(.URL)
                                .autocorrectionDisabled()
                                .textContentType(.URL)
                                .modifier(EMSFieldStyle(theme: theme, invalid: validationError != nil))
                                .onChange(of: baseURL) { _, _ in validationError = nil }

                            if useTokenLogin {
                                fieldLabel("Access token")
                                SecureField("Paste an access token", text: $accessToken)
                                    .textInputAutocapitalization(.never)
                                    .autocorrectionDisabled()
                                    .textContentType(.password)
                                    .modifier(EMSFieldStyle(theme: theme, invalid: false))
                            } else {
                                fieldLabel("Username")
                                TextField("username", text: $username)
                                    .textInputAutocapitalization(.never)
                                    .autocorrectionDisabled()
                                    .textContentType(.username)
                                    .modifier(EMSFieldStyle(theme: theme, invalid: false))

                                fieldLabel("Password")
                                SecureField("password", text: $password)
                                    .textInputAutocapitalization(.never)
                                    .autocorrectionDisabled()
                                    .textContentType(.password)
                                    .submitLabel(.go)
                                    .onSubmit { Task { await submit() } }
                                    .modifier(EMSFieldStyle(theme: theme, invalid: false))
                            }

                            if showsHTTPWarning {
                                Text("Local HTTP can expose your credentials to devices on the same trusted LAN or VPN.")
                                    .font(.footnote)
                                    .foregroundStyle(themeColor(theme.amber))
                            }

                            Button(useTokenLogin ? "Use username & password" : "Use an access token instead") {
                                useTokenLogin.toggle()
                                validationError = nil
                            }
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(themeColor(theme.accent))
                            .padding(.top, 2)
                        }
                        .padding(20)
                        .background(themeColor(theme.panel))
                        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                        .overlay {
                            RoundedRectangle(cornerRadius: 16, style: .continuous)
                                .stroke(themeColor(theme.line), lineWidth: 1)
                        }

                        VStack(spacing: 12) {
                            Button(connectButtonTitle) {
                                Task { await submit() }
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
        .onAppear(perform: prefillSavedServer)
    }

    private func fieldLabel(_ text: String) -> some View {
        Text(text)
            .font(.caption.weight(.semibold))
            .foregroundStyle(themeColor(theme.muted))
    }

    private var connectButtonTitle: String {
        if isConnecting { return "Connecting..." }
        return useTokenLogin ? "Connect" : "Sign In"
    }

    private var trimmedToken: String {
        accessToken.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var showsHTTPWarning: Bool {
        let http = baseURL.lowercased().trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("http://")
        guard http else { return false }
        return useTokenLogin ? !trimmedToken.isEmpty : !password.isEmpty
    }

    // Prefill the last server we talked to (e.g. after a session-expiry bounce) so the operator only
    // has to re-enter credentials, not the address. Runs once.
    private func prefillSavedServer() {
        guard !didPrefill else { return }
        didPrefill = true
        if let saved = try? credentialStore.lastBaseURL() {
            baseURL = saved.absoluteString
        }
    }

    private func submit() async {
        if useTokenLogin {
            await connectWithToken()
        } else {
            await signIn()
        }
    }

    // Primary flow: username/password → session token + provisioned per-device widget access token.
    private func signIn() async {
        isConnecting = true
        defer { isConnecting = false }
        do {
            let url = try discovery.normalizedManualURL(baseURL)
            try await dashboardStore.login(
                baseURL: url,
                username: username,
                password: password,
                deviceName: UIDevice.current.name
            )
            // login() calls refresh(); if the dashboard couldn't load, surface why (but we ARE
            // signed in — the token is stored, a pull-to-refresh will recover).
            if dashboardStore.snapshot == nil {
                validationError = dashboardStore.lastError ?? "Signed in, but couldn't load the dashboard."
            } else {
                validationError = nil
            }
        } catch let error as APIClientError {
            validationError = loginErrorMessage(error)
        } catch let urlError as URLError {
            validationError = networkErrorMessage(urlError)
        } catch {
            validationError = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }

    // Fallback flow for machine-token users: paste a long-lived access token. Only the LITERAL
    // paste-field contents are ever treated as an access token and fed to the widget — see
    // DashboardStore.saveConnectedServer. When the paste field is empty this falls back to
    // whatever token is already saved for this server (typically the interactive/SESSION token
    // from a password login) so the app can still connect, but that fallback token must NEVER be
    // mirrored to the widget (spec §7 invariant — the widget only ever carries a dedicated access
    // token).
    private func connectWithToken() async {
        isConnecting = true
        defer { isConnecting = false }
        do {
            let url = try discovery.normalizedManualURL(baseURL)
            let pastedToken = trimmedToken.nilIfEmpty
            let savedToken = (try? credentialStore.token(for: url)) ?? ""
            let token = pastedToken ?? savedToken.nilIfEmpty
            let client = APIClient(baseURL: url, token: token)

            _ = try await client.fetchLiveHealth()
            _ = try await client.fetchReadyHealth()
            let auth = try await client.fetchAuthStatus()

            if auth.required && (token ?? "").isEmpty {
                validationError = "This EMS requires an access token."
                return
            }
            if auth.required && !auth.authenticated {
                validationError = "Access token rejected."
                return
            }
            validationError = nil
            dashboardStore.client = client
            // widgetAccessToken is the pasted token ONLY — if the field was empty, `token` may be a
            // fallback session-slot token, which must never reach the widget.
            try? dashboardStore.saveConnectedServer(client: client, widgetAccessToken: pastedToken)
            await dashboardStore.refresh()
        } catch let error as APIClientError {
            validationError = loginErrorMessage(error)
        } catch let urlError as URLError {
            validationError = networkErrorMessage(urlError)
        } catch {
            validationError = (error as? LocalizedError)?.errorDescription ?? String(describing: error)
        }
    }

    private func loginErrorMessage(_ error: APIClientError) -> String {
        switch error {
        case .httpStatus(401):
            return useTokenLogin ? "Access token rejected." : "Invalid username or password."
        case let .httpStatus(code):
            return "The server returned an error (HTTP \(code))."
        case .invalidResponse:
            return "The server sent a response the app couldn't read."
        case let .incompatibleServer(version):
            return "This app isn't compatible with the EMS server (API v\(version))."
        }
    }

    private func networkErrorMessage(_ error: URLError) -> String {
        "Couldn't reach the server. Check the address and your connection."
    }
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}

private struct EMSFieldStyle: ViewModifier {
    let theme: EMSTheme
    let invalid: Bool

    func body(content: Content) -> some View {
        content
            .padding(14)
            .background(themeColor(theme.secondaryPanel))
            .foregroundStyle(themeColor(theme.text))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(themeColor(invalid ? theme.error : theme.line), lineWidth: 1)
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
