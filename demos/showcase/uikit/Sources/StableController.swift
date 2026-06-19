import UIKit

/// Tab: Stable (SPEC §5.1). Catalog list with async load; rows push Horse Detail.
/// A refresh button re-fetches the catalog (GET apiBase/horses) and mirrors loading→
/// done/error to `stable.status`.
final class StableController: UITableViewController {
    private let model: AppModel
    private var horses: [Horse] = []

    private let statusLabel = UILabel()
    private let emptyLabel = UILabel()

    init(model: AppModel) {
        self.model = model
        super.init(style: .insetGrouped)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Stable"

        // Nav title carries the id (SPEC lists stable.title as the nav title "Stable").
        navigationItem.titleView = makeTitleView("Stable").accessibilityID("stable.title")

        let refresh = UIBarButtonItem(
            image: UIImage(systemName: "arrow.clockwise"),
            primaryAction: UIAction { [weak self] _ in self?.reload() })
        refresh.accessibilityID("stable.refresh")
        navigationItem.rightBarButtonItem = refresh

        statusLabel.font = .preferredFont(forTextStyle: .footnote)
        statusLabel.textColor = .secondaryLabel
        statusLabel.accessibilityID("stable.status")
        setStatus(.idle)
        let header = UIView(frame: CGRect(x: 0, y: 0, width: 0, height: 36))
        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        header.addSubview(statusLabel)
        NSLayoutConstraint.activate([
            statusLabel.leadingAnchor.constraint(equalTo: header.leadingAnchor, constant: 20),
            statusLabel.centerYAnchor.constraint(equalTo: header.centerYAnchor),
        ])
        tableView.tableHeaderView = header

        emptyLabel.text = "No horses in the stable"
        emptyLabel.textColor = .secondaryLabel
        emptyLabel.textAlignment = .center
        emptyLabel.accessibilityID("stable.empty")

        // Async load (SPEC §5.1): show the seeded catalog after a short delay.
        loadCatalog()
    }

    private func loadCatalog() {
        let delay: TimeInterval = model.animationsDisabled ? 0 : 0.4
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self else { return }
            horses = model.horses
            tableView.reloadData()
            updateEmptyState()
        }
    }

    private func reload() {
        ShowcaseNet.get("\(model.apiBase)/horses") { [weak self] status in
            self?.setStatus(status)
        }
    }

    private func setStatus(_ status: ShowcaseNet.Status) {
        statusLabel.text = "Status: \(status.rawValue)"
        statusLabel.accessibilityStateValue(status.rawValue)
    }

    private func updateEmptyState() {
        // SPEC §5.1: stable.empty shown only when the catalog is empty.
        tableView.backgroundView = horses.isEmpty ? emptyLabel : nil
    }

    // MARK: - Table

    override func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        horses.count
    }

    override func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = UITableViewCell(style: .default, reuseIdentifier: nil)
        let horse = horses[indexPath.row]
        cell.textLabel?.text = horse.name
        cell.accessoryType = .disclosureIndicator
        cell.accessibilityID("stable.row.\(horse.id)")  // data-derived, unique per row (SPEC §5.1)
        return cell
    }

    override func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: !model.animationsDisabled)
        let horse = horses[indexPath.row]
        let detail = HorseDetailController(horse: horse, model: model)
        navigationController?.pushViewController(detail, animated: !model.animationsDisabled)
    }
}
