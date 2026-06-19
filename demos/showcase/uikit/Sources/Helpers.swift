import UIKit

extension UIViewController {
    /// A label suitable for `navigationItem.titleView`, so the nav title can carry a
    /// stable identifier via aid(...) (the standard large-title label is not directly
    /// addressable). The text still reads correctly to VoiceOver.
    func makeTitleView(_ text: String) -> UILabel {
        let label = UILabel()
        label.text = text
        label.font = .preferredFont(forTextStyle: .headline)
        label.sizeToFit()
        return label
    }

    /// Replace the system back button with an identifier-carrying custom one (reserved
    /// `nav` namespace, SPEC §5.1). The default back button is not directly addressable,
    /// so a pushed screen installs its own that still pops the stack. Call from
    /// viewDidLoad.
    func installBackButton() {
        let back = UIBarButtonItem(
            image: UIImage(systemName: "chevron.backward"),
            primaryAction: UIAction { [weak self] _ in
                self?.navigationController?.popViewController(animated: true)
            })
        back.title = "Back"
        back.accessibilityID("nav.back")
        navigationItem.leftBarButtonItem = back
    }
}
