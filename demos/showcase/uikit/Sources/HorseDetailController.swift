import UIKit

/// Horse Detail (SPEC §5.1) — pushed from Stable or reached via …://horse/<id>.
/// Fetch button GETs apiBase/horses/<id>; a favorite toggle reflects via `selected`
/// trait and mirrors on/off to horse.favorite.value.
final class HorseDetailController: UIViewController {
    private let horse: Horse
    private let model: AppModel

    private let statusLabel = UILabel()
    private let favoriteButton = UIButton(type: .system)
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
        view.backgroundColor = .systemBackground
        title = horse.name
        installBackButton()
        navigationItem.titleView = makeTitleView(horse.name).accessibilityID("horse.title")

        let idLabel = UILabel()
        idLabel.text = "ID: \(horse.id)"
        idLabel.accessibilityID("horse.id.value")
        idLabel.accessibilityStateValue(String(horse.id))

        let fetch = UIButton(type: .system, primaryAction: UIAction(title: "Fetch detail") { [weak self] _ in
            self?.fetch()
        })
        fetch.configuration = .bordered()
        fetch.accessibilityID("horse.fetch")

        statusLabel.accessibilityID("horse.status")
        setStatus(.idle)

        updateFavoriteUI()
        favoriteButton.addAction(UIAction { [weak self] _ in self?.toggleFavorite() }, for: .primaryActionTriggered)
        favoriteButton.accessibilityID("horse.favorite")

        let stack = UIStackView(arrangedSubviews: [idLabel, fetch, statusLabel, favoriteButton])
        stack.axis = .vertical
        stack.spacing = 20
        stack.alignment = .leading
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 24),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
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
        favoriteButton.setTitle(isFavorite ? "Favorited" : "Favorite", for: .normal)
        // selected trait reflects state (SPEC §5.1).
        if isFavorite {
            favoriteButton.accessibilityTraits.insert(.selected)
        } else {
            favoriteButton.accessibilityTraits.remove(.selected)
        }
        favoriteButton.accessibilityStateValue(isFavorite ? "on" : "off")
    }
}
