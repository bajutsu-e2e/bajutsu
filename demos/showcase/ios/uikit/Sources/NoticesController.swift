import UIKit

/// Tab: Notices (SPEC §5.5). A plain vertical list of three notices; tapping a row
/// pushes its detail. The smallest list → detail flow, distinct from the data-loading
/// Stable catalog — a clean target for navigation scenarios and crawl.
final class NoticesController: UITableViewController {
    private let model: AppModel
    private var notices: [Notice] { model.notices }

    init(model: AppModel) {
        self.model = model
        super.init(style: .insetGrouped)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Notices"
    }

    override func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        notices.count
    }

    override func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = UITableViewCell(style: .default, reuseIdentifier: nil)
        let notice = notices[indexPath.row]
        cell.textLabel?.text = notice.title
        cell.accessoryType = .disclosureIndicator
        cell.accessibilityID("notice.row.\(notice.id)")  // data-derived, unique per row
        return cell
    }

    override func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: !model.animationsDisabled)
        let notice = notices[indexPath.row]
        navigationController?.pushViewController(
            NoticeDetailController(notice: notice), animated: !model.animationsDisabled)
    }
}

/// Notice Detail (SPEC §5.5) — pushed from the Notices list or via …://notice/<id>.
/// Shows the notice's title and body; the system back button pops the stack.
final class NoticeDetailController: UIViewController {
    private let notice: Notice

    init(notice: Notice) {
        self.notice = notice
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = notice.title
        navigationItem.largeTitleDisplayMode = .never  // inline title, like the SwiftUI detail
        // The standard system back button pops back; the backend drives it by its built-in id `BackButton`.

        let titleLabel = UILabel()
        titleLabel.text = notice.title
        titleLabel.font = .preferredFont(forTextStyle: .title2)
        titleLabel.numberOfLines = 0
        titleLabel.accessibilityID("notice.detail.title")

        let bodyLabel = UILabel()
        bodyLabel.text = notice.body
        bodyLabel.textColor = .secondaryLabel
        bodyLabel.numberOfLines = 0
        bodyLabel.accessibilityID("notice.detail.body")

        // A grouped form mirroring the SwiftUI Notice Detail.
        installGroupedForm([makeSectionCard([titleLabel, bodyLabel])])
    }
}
