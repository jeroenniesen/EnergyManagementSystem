import SwiftUI
import EMSControlCore

/// App Settings: privacy/support links + forget server / sign out (#257).
struct SettingsView: View {
    @Environment(DashboardStore.self) private var dashboardStore
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.dismiss) private var dismiss
    @State private var confirmForget = false

    /// When false (connection screen), hide the sign-out action — there is nothing to forget.
    var showsSignOut: Bool = true

    private var theme: EMSTheme {
        colorScheme == .dark ? .dark : .light
    }

    private var isDemo: Bool {
        dashboardStore.snapshot?.isDemo == true
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Link(destination: AppLinks.privacyPolicyURL()) {
                        Label("Privacy Policy", systemImage: "hand.raised")
                    }
                    Link(destination: AppLinks.supportURL()) {
                        Label("Support", systemImage: "questionmark.circle")
                    }
                } header: {
                    Text("About")
                } footer: {
                    Text("Opens in your browser.")
                }

                if showsSignOut, dashboardStore.snapshot != nil || dashboardStore.client != nil {
                    Section {
                        Button(role: .destructive) {
                            confirmForget = true
                        } label: {
                            Label(
                                isDemo ? "Leave Demo" : "Forget Server / Sign Out",
                                systemImage: "rectangle.portrait.and.arrow.right"
                            )
                        }
                    } header: {
                        Text("Server")
                    } footer: {
                        Text(
                            isDemo
                                ? "Returns to the connection screen."
                                : "Removes the saved server address, tokens, and shared widget data from this device."
                        )
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(themeColor(theme.background))
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .confirmationDialog(
                isDemo ? "Leave demo?" : "Forget this server?",
                isPresented: $confirmForget,
                titleVisibility: .visible
            ) {
                Button(isDemo ? "Leave Demo" : "Forget Server / Sign Out", role: .destructive) {
                    dashboardStore.forgetServer()
                    dismiss()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(
                    isDemo
                        ? "You can reconnect or open the demo again from the connection screen."
                        : "This device will forget the server address and tokens. Your EMS server is not deleted."
                )
            }
        }
    }
}
