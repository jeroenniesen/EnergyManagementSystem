import Foundation

public struct DemoDataStore {
    private let bundle: Bundle

    public init(bundle: Bundle? = nil) {
        self.bundle = bundle ?? Self.defaultBundle
    }

    public func dashboardSnapshot() throws -> DashboardSnapshot {
        try decode(DashboardSnapshot.self, resource: "demo-dashboard")
    }

    public func faq() throws -> FAQResponse {
        try decode(FAQResponse.self, resource: "demo-faq")
    }

    public func explainerStatus() throws -> ExplainerStatus {
        try decode(ExplainerStatus.self, resource: "demo-explainer")
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

private extension DemoDataStore {
    static var defaultBundle: Bundle {
#if SWIFT_PACKAGE
        .module
#else
        Bundle(for: BundleToken.self)
#endif
    }
}

private final class BundleToken {}
