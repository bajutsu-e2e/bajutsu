package com.bajutsu.showcase.views

import android.annotation.SuppressLint
import android.app.Dialog
import android.graphics.Color
import android.text.InputType
import android.view.Gravity
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetDialog
import kotlinx.coroutines.launch

// Tab: Log (SPEC §5.3) — a training-log composer exercising every input control, dedicated gesture
// targets, and all four modal styles: bottom sheet, full-screen cover, a custom action-sheet overlay,
// and an auto-dismissing toast (~1.2 s → exercises `wait until gone`). Each control mirrors its
// result so a scenario can assert it landed.
@SuppressLint("ClickableViewAccessibility")
class LogTab(
    private val activity: MainActivity,
    private val model: AppModel,
    private val overlay: FrameLayout,
) {
    val root: View

    private var count = 1
    private var intense = false
    private var segment = "one"
    private var nextRow = 1

    // Live modal references, so a deeplink can dismiss them (SPEC §4). The sheet/cover are separate
    // windows and the scrim/toast live in the shared overlay; none is closed by a plain tab switch.
    private var currentSheet: BottomSheetDialog? = null
    private var currentCover: Dialog? = null
    private var scrimView: View? = null
    private var toastView: View? = null

    init {
        val note = EditText(activity).apply {
            hint = "Note"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            minLines = 3
        }.aid("log_note")

        // Stepper: the increment button carries log.count; the value mirrors to log.count.value.
        val countValue = activity.secondaryLabel("Count: $count").aid("log_count_value").stateValue(count.toString())
        val decrement = activity.textButton("−") {
            if (count > 0) count--
            countValue.text = "Count: $count"
            countValue.stateValue(count.toString())
        }
        val increment = activity.textButton("Count +") {
            if (count < 99) count++
            countValue.text = "Count: $count"
            countValue.stateValue(count.toString())
        }.aid("log_count")

        // A button-backed toggle (iOS parity); isSelected reflects the state, value mirrors on/off.
        val intenseValue = activity.secondaryLabel("Easy").aid("log_intense_value").stateValue("off")
        val intenseButton = activity.textButton("☐ Intense") {}.aid("log_intense")
        intenseButton.setOnClickListener {
            intense = !intense
            intenseButton.text = if (intense) "☑ Intense" else "☐ Intense"
            intenseButton.isSelected = intense
            intenseValue.text = if (intense) "Intense" else "Easy"
            intenseValue.stateValue(if (intense) "on" else "off")
        }

        val status = activity.secondaryLabel("Status: idle").aid("log_status").stateValue("idle")
        val entries = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        val submit = activity.textButton("Submit") {
            status.text = "Status: loading"
            status.stateValue("loading")
            activity.lifecycleScope.launch {
                val result = Net.postLog(model.httpBase, note.text.toString(), count, intense)
                status.text = "Status: $result"
                status.stateValue(result)
                if (result == "done") {
                    entries.addView(activity.label("Entry $nextRow").aid("log_row_$nextRow"))
                    nextRow++
                    showToast()
                }
            }
        }.aid("log_submit")

        // Modals — the four presentation styles.
        val dialogValue = activity.secondaryLabel("Dialog: none").aid("log_dialog_value").stateValue("none")
        val openFilter = activity.textButton("Open Filter") { showSheet() }.aid("log_openFilter")
        val openGallery = activity.textButton("Open Gallery") { showCover() }.aid("log_openGallery")
        val openDelete = activity.textButton("Open Delete") { showActionSheet(dialogValue) }.aid("log_openDelete")

        // Gesture targets: a long-press and a double-tap, each mirroring its result. Both sit below
        // the form's fold on a phone screen, so a run scrolls them into view first (SPEC §5.3).
        val longpressValue = activity.secondaryLabel("idle").aid("log_longpress_value").stateValue("idle")
        val longpress = activity.label("Long-press me").aid("log_longpress").apply {
            setOnLongClickListener {
                longpressValue.text = "pressed"
                longpressValue.stateValue("pressed")
                true
            }
        }

        var doubleTaps = 0
        val doubletapValue = activity.secondaryLabel("Double-taps: 0").aid("log_doubletap_value").stateValue("0")
        val doubletap = activity.label("Double-tap me").aid("log_doubletap")
        val detector = GestureDetector(activity, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDoubleTap(e: MotionEvent): Boolean {
                doubleTaps++
                doubletapValue.text = "Double-taps: $doubleTaps"
                doubletapValue.stateValue(doubleTaps.toString())
                return true
            }
        })
        doubletap.setOnTouchListener { _, event -> detector.onTouchEvent(event); true }

        // A button-backed segmented control; the selected button reflects the pick, the choice
        // mirrors to log.segment.value.
        val segmentValue = activity.secondaryLabel("Segment: one").aid("log_segment_value").stateValue("one")
        val segmentButtons = mutableMapOf<String, Button>()
        val segmentRow = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
        listOf("one", "two", "three").forEach { choice ->
            val button = activity.textButton(segmentTitle(choice, selected = choice == segment)) {
                segment = choice
                segmentButtons.forEach { (name, b) ->
                    b.text = segmentTitle(name, selected = name == choice)
                    b.isSelected = name == choice
                }
                segmentValue.text = "Segment: $choice"
                segmentValue.stateValue(choice)
            }.aid("log_segment_$choice")
            button.isSelected = choice == segment
            segmentButtons[choice] = button
            segmentRow.addView(button)
        }

        val form = activity.vstack(
            note,
            activity.hstack(decrement, increment),
            countValue,
            intenseButton,
            intenseValue,
            submit,
            status,
            openFilter,
            openGallery,
            openDelete,
            dialogValue,
            longpress,
            longpressValue,
            doubletap,
            doubletapValue,
            segmentRow,
            segmentValue,
            entries,
        )
        root = activity.vstack(activity.header("Log"), activity.scrollPage(form)).apply {
            (getChildAt(1).layoutParams as LinearLayout.LayoutParams).apply { height = 0; weight = 1f }
        }
    }

    private fun segmentTitle(choice: String, selected: Boolean): String {
        val name = choice.replaceFirstChar { it.uppercase() }
        return if (selected) "● $name" else name
    }

    // Dismiss any open modal — invoked by the deeplink path (SPEC §4: a deeplink dismisses modals).
    fun dismissModals() {
        currentSheet?.dismiss()
        currentCover?.dismiss()
        scrimView?.let { overlay.removeView(it) }
        scrimView = null
        toastView?.let { overlay.removeView(it) }
        toastView = null
    }

    // Sheet (SPEC §5.3): a Material bottom sheet, the Views analog of the detented iOS sheet.
    private fun showSheet() {
        val sheet = BottomSheetDialog(activity)
        val content = activity.vstack(
            activity.label("Filter").aid("log_sheet_title"),
            activity.textButton("Apply") { sheet.dismiss() }.aid("log_sheet_apply"),
            activity.textButton("Close") { sheet.dismiss() }.aid("log_sheet_close"),
        ).apply { setPadding(activity.dp(24), activity.dp(24), activity.dp(24), activity.dp(24)) }
        sheet.setContentView(content)
        sheet.setOnDismissListener { if (currentSheet === sheet) currentSheet = null }
        currentSheet = sheet
        sheet.show()
    }

    // Full-screen cover: a fullscreen Dialog, the Views analog of the iOS fullScreenCover.
    private fun showCover() {
        val cover = Dialog(activity, android.R.style.Theme_Material_Light_NoActionBar_Fullscreen)
        cover.setContentView(
            activity.vstack(
                activity.label("Gallery").aid("log_cover_title"),
                activity.textButton("Close") { cover.dismiss() }.aid("log_cover_close"),
            ).apply { setPadding(activity.dp(24), activity.dp(24), activity.dp(24), activity.dp(24)) },
        )
        cover.setOnDismissListener { if (currentCover === cover) currentCover = null }
        currentCover = cover
        cover.show()
    }

    // Action sheet: a custom overlay of plain buttons (iOS parity — SPEC §5.3 keeps this out of the
    // native dialog machinery). The scrim is clickable so it consumes touches — otherwise taps outside
    // the card fall through to the form controls beneath while the "modal" is up. Result mirrors to
    // log.dialog.value.
    private fun showActionSheet(dialogValue: android.widget.TextView) {
        val scrim = FrameLayout(activity).apply {
            setBackgroundColor(Color.argb(51, 0, 0, 0))
            isClickable = true
            isFocusable = true
        }
        fun choose(result: String?) {
            overlay.removeView(scrim)
            if (scrimView === scrim) scrimView = null
            if (result != null) {
                dialogValue.text = "Dialog: $result"
                dialogValue.stateValue(result)
            }
        }
        val card = activity.vstack(
            activity.label("Delete entry").aid("log_dialog_title"),
            activity.textButton("Archive") { choose("archive") }.aid("log_dialog_archive"),
            activity.textButton("Delete") { choose("delete") }.aid("log_dialog_delete"),
            activity.textButton("Cancel") { choose(null) }.aid("log_dialog_cancel"),
        ).apply {
            setBackgroundColor(Color.WHITE)
            setPadding(activity.dp(24), activity.dp(24), activity.dp(24), activity.dp(24))
        }
        scrim.addView(
            card,
            FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.CENTER),
        )
        scrimView = scrim
        overlay.addView(scrim, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
    }

    // The transient toast (~1.2 s auto-dismiss → exercises `wait until gone`). Only one toast is ever
    // present: a rapid second submit removes the previous one before adding a new one, so log_toast
    // never resolves to two nodes (an ambiguous selector the runner would fail fast on).
    private fun showToast() {
        toastView?.let { overlay.removeView(it) }
        val toast = activity.label("Saved").aid("log_toast").apply { setBackgroundColor(Color.LTGRAY) }
        toastView = toast
        overlay.addView(
            toast,
            FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.CENTER_HORIZONTAL).apply {
                topMargin = activity.dp(24)
            },
        )
        toast.postDelayed({
            if (toastView === toast) {
                overlay.removeView(toast)
                toastView = null
            }
        }, 1200)
    }
}
