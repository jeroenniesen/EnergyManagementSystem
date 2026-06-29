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
