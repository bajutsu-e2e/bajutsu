import UIKit

/// Horse Detail (SPEC §5.1) — pushed from Stable or reached via …://horse/<id>.
/// Fetch button GETs apiBase/horses/<id>; a favorite toggle reflects via `selected`
/// trait and mirrors on/off to horse.favorite.value.
final class HorseDetailController: UIViewController {
    private let horse: Horse
    private let model: AppModel

    private let statusLabel = UILabel()
    private let favoriteButton = UIButton(type: .system)
    private let favoriteValueLabel = UILabel()
    private var isFavorite = false

    init(horse: Horse, model: AppModel) {
        self.horse = horse
        self.model = model
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = horse.name
        navigationItem.largeTitleDisplayMode = .never  // inline title, like the SwiftUI detail
        // The horse's name shown in the body — the screen's identifying content leaf
        // (horse.title), matching the SwiftUI twin (the nav title carries no id).
        let titleLabel = UILabel()
        titleLabel.text = horse.name
        titleLabel.font = .preferredFont(forTextStyle: .title2)
        titleLabel.accessibilityID("horse.title")

        let idLabel = UILabel()
        idLabel.text = "ID: \(horse.id)"
        idLabel.accessibilityID("horse.id.value")
        idLabel.accessibilityStateValue(String(horse.id))

        let fetch = UIButton(type: .system, primaryAction: UIAction(title: "Fetch detail") { [weak self] _ in
            self?.fetch()
        })
        fetch.contentHorizontalAlignment = .leading
        fetch.accessibilityID("horse.fetch")

        statusLabel.accessibilityID("horse.status")
        setStatus(.idle)

        favoriteButton.addAction(UIAction { [weak self] _ in self?.toggleFavorite() }, for: .primaryActionTriggered)
        favoriteButton.contentHorizontalAlignment = .leading
        favoriteButton.accessibilityID("horse.favorite")
        favoriteValueLabel.font = .preferredFont(forTextStyle: .footnote)
        favoriteValueLabel.textColor = .secondaryLabel
        favoriteValueLabel.accessibilityID("horse.favorite.value")  // separate mirror (matches SwiftUI)
        updateFavoriteUI()

        // A grouped form mirroring the SwiftUI Horse Detail sections.
        installGroupedForm([
            makeSectionCard([titleLabel, idLabel]),
            makeSectionCard([fetch, statusLabel]),
            makeSectionCard([favoriteButton, favoriteValueLabel]),
        ])
    }

    private func fetch() {
        ShowcaseNet.get("\(model.apiBase)/horses/\(horse.id)") { [weak self] status in
            self?.setStatus(status)
        }
    }

    private func setStatus(_ status: ShowcaseNet.Status) {
        statusLabel.text = "Status: \(status.rawValue)"
        statusLabel.accessibilityStateValue(status.rawValue)
    }

    private func toggleFavorite() {
        isFavorite.toggle()
        updateFavoriteUI()
    }

    private func updateFavoriteUI() {
        favoriteButton.setTitle(isFavorite ? " Favorited" : " Favorite", for: .normal)
        // A star icon, like the SwiftUI Label("Favorite", systemImage: "star").
        favoriteButton.setImage(UIImage(systemName: isFavorite ? "star.fill" : "star"), for: .normal)
        // selected trait reflects state (SPEC §5.1); the value mirrors to a separate label.
        favoriteButton.accessibilityTraits = isFavorite ? [.button, .selected] : [.button]
        favoriteValueLabel.text = isFavorite ? "Favorited" : "Not favorited"
        favoriteValueLabel.accessibilityStateValue(isFavorite ? "on" : "off")
    }
}
