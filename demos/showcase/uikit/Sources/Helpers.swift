import UIKit

extension UIViewController {
    /// A grouped "section" card — rounded, on a `.secondarySystemGroupedBackground` fill — wrapping
    /// a vertical stack of rows. The UIKit echo of a SwiftUI Form `Section`, so the two toolkits
    /// read alike (SPEC §2). Pair with `makeSectionHeader` and a `.systemGroupedBackground` view.
    func makeSectionCard(_ rows: [UIView], spacing: CGFloat = 12) -> UIView {
        let stack = UIStackView(arrangedSubviews: rows)
        stack.axis = .vertical
        stack.spacing = spacing
        stack.alignment = .fill
        stack.isLayoutMarginsRelativeArrangement = true
        stack.layoutMargins = UIEdgeInsets(top: 14, left: 16, bottom: 14, right: 16)
        stack.translatesAutoresizingMaskIntoConstraints = false

        let card = UIView()
        card.backgroundColor = .secondarySystemGroupedBackground
        card.layer.cornerRadius = 12
        card.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: card.topAnchor),
            stack.bottomAnchor.constraint(equalTo: card.bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: card.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: card.trailingAnchor),
        ])
        return card
    }

    /// A section-header label (small, secondary) above a `makeSectionCard`, like a Form section title.
    func makeSectionHeader(_ text: String) -> UILabel {
        let label = UILabel()
        label.text = text
        label.font = .preferredFont(forTextStyle: .subheadline)
        label.textColor = .secondaryLabel
        return label
    }

    /// Wrap a vertical stack of section headers/cards in a scrolling, grouped-background page —
    /// the UIKit echo of a SwiftUI `Form`. Returns the outer content stack so the caller can read
    /// its arranged subviews if needed.
    @discardableResult
    func installGroupedForm(_ sections: [UIView], topInset: CGFloat = 16) -> UIStackView {
        view.backgroundColor = .systemGroupedBackground
        let content = UIStackView(arrangedSubviews: sections)
        content.axis = .vertical
        content.spacing = 10
        content.translatesAutoresizingMaskIntoConstraints = false

        let scroll = UIScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.alwaysBounceVertical = true
        scroll.addSubview(content)
        view.addSubview(scroll)

        let guide = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            scroll.topAnchor.constraint(equalTo: guide.topAnchor),
            scroll.leadingAnchor.constraint(equalTo: guide.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: guide.trailingAnchor),
            scroll.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            content.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor, constant: topInset),
            content.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor, constant: -24),
            content.leadingAnchor.constraint(equalTo: scroll.frameLayoutGuide.leadingAnchor, constant: 16),
            content.trailingAnchor.constraint(equalTo: scroll.frameLayoutGuide.trailingAnchor, constant: -16),
        ])
        return content
    }
}
