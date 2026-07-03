import UIKit

/// The detented sheet reached via log.openFilter (SPEC §5.3).
final class FilterSheetController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground

        let title = UILabel()
        title.text = "Filter"
        title.font = .preferredFont(forTextStyle: .title1)
        title.accessibilityID("log.sheet.title")

        let apply = UIButton(type: .system, primaryAction: UIAction(title: "Apply") { [weak self] _ in
            self?.dismiss(animated: true)
        })
        apply.configuration = .borderedProminent()
        apply.accessibilityID("log.sheet.apply")

        let close = UIButton(type: .system, primaryAction: UIAction(title: "Close") { [weak self] _ in
            self?.dismiss(animated: true)
        })
        close.accessibilityID("log.sheet.close")

        let stack = UIStackView(arrangedSubviews: [title, apply, close])
        stack.axis = .vertical
        stack.spacing = 20
        stack.alignment = .center
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 40),
        ])
    }
}

/// The full-screen cover reached via log.openGallery (SPEC §5.3).
final class GalleryCoverController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground

        let title = UILabel()
        title.text = "Gallery"
        title.font = .preferredFont(forTextStyle: .title1)
        title.accessibilityID("log.cover.title")

        // Plain Close (not filled) and top-anchored, matching the SwiftUI fullScreenCover.
        let close = UIButton(type: .system, primaryAction: UIAction(title: "Close") { [weak self] _ in
            self?.dismiss(animated: true)
        })
        close.accessibilityID("log.cover.close")

        let stack = UIStackView(arrangedSubviews: [title, close])
        stack.axis = .vertical
        stack.spacing = 16
        stack.alignment = .center
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 40),
        ])
    }
}
