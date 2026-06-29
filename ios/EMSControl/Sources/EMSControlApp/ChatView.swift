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
