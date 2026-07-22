import UIKit

/// Tab: Stable (SPEC §5.1). Catalog list with async load; rows push Horse Detail. A refresh
/// button re-fetches the catalog (GET apiBase/horses) and mirrors loading→done/error to
/// `stable.status`. Laid out to mirror the SwiftUI twin: a grouped list with a bottom status line.
final class StableController: UIViewController, UITableViewDataSource, UITableViewDelegate {
    private let model: AppModel
    private var horses: [Horse] = []

    private let tableView = UITableView(frame: .zero, style: .insetGrouped)
    private let statusLabel = UILabel()

    init(model: AppModel) {
        self.model = model
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemGroupedBackground
        title = "Stable"

        let refresh = UIBarButtonItem(title: "Refresh", primaryAction: UIAction { [weak self] _ in self?.reload() })
        refresh.accessibilityID("stable.refresh")
        navigationItem.rightBarButtonItem = refresh

        tableView.dataSource = self
        tableView.delegate = self
        tableView.backgroundColor = .clear
        tableView.translatesAutoresizingMaskIntoConstraints = false

        // Bottom status line, like the SwiftUI safeAreaInset(edge: .bottom).
        statusLabel.font = .preferredFont(forTextStyle: .footnote)
        statusLabel.textColor = .secondaryLabel
        statusLabel.textAlignment = .center
        statusLabel.accessibilityID("stable.status")
        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        setStatus(.idle)

        view.addSubview(tableView)
        view.addSubview(statusLabel)
        let guide = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            tableView.topAnchor.constraint(equalTo: guide.topAnchor),
            tableView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            tableView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            tableView.bottomAnchor.constraint(equalTo: statusLabel.topAnchor, constant: -8),

            statusLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            statusLabel.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),
            statusLabel.bottomAnchor.constraint(equalTo: guide.bottomAnchor, constant: -8),
        ])

        // Async load (SPEC §5.1): show the seeded catalog after a short delay.
        loadCatalog()
    }

    private func loadCatalog() {
        let delay: TimeInterval = model.animationsDisabled ? 0 : 0.4
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self else { return }
            horses = model.horses
            tableView.reloadData()
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

    // MARK: - Table

    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        // SPEC §5.1: an empty catalog shows a single placeholder row (stable.empty). A cell
        // surfaces to the backend where tableView.backgroundView does not on iOS 26.
        horses.isEmpty ? 1 : horses.count
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = UITableViewCell(style: .default, reuseIdentifier: nil)
        if horses.isEmpty {
            cell.textLabel?.text = "No horses in the stable"
            cell.textLabel?.textColor = .secondaryLabel
            cell.selectionStyle = .none
            cell.accessibilityID("stable.empty")
            return cell
        }
        let horse = horses[indexPath.row]
        cell.textLabel?.text = horse.name
        cell.accessoryType = .disclosureIndicator
        cell.accessibilityID("stable.row.\(horse.id)")  // data-derived, unique per row (SPEC §5.1)
        return cell
    }

    func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: !model.animationsDisabled)
        guard !horses.isEmpty else { return }  // the empty placeholder row is inert
        let horse = horses[indexPath.row]
        let detail = HorseDetailController(horse: horse, model: model)
        navigationController?.pushViewController(detail, animated: !model.animationsDisabled)
    }
}
