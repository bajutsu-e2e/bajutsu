package com.bajutsu.showcase.views

import android.content.Context
import android.util.TypedValue
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

// The Views twin of the UIKit Helpers.swift: tiny programmatic builders so the tab classes read
// like their UIKit counterparts. All UI is built in code — identifiers enter only through
// View.aid(...) (Accessibility.kt), never a layout file, so the a11y/noax twin stays a single seam.

fun Context.dp(value: Int): Int =
    TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value.toFloat(), resources.displayMetrics).toInt()

/** The screen title label — display only, no id (SPEC §5.1: screen titles carry no id). */
fun Context.header(text: String): TextView = TextView(this).apply {
    this.text = text
    setTextSize(TypedValue.COMPLEX_UNIT_SP, 24f)
    setPadding(dp(16), dp(16), dp(16), dp(8))
}

fun Context.label(text: String): TextView = TextView(this).apply {
    this.text = text
    setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
    setPadding(dp(16), dp(8), dp(16), dp(8))
}

/** A secondary (state-mirroring) label; pair with .stateValue(...) so assertions can read it. */
fun Context.secondaryLabel(text: String): TextView = label(text).apply { alpha = 0.6f }

fun Context.textButton(text: String, onClick: (View) -> Unit): Button = Button(this).apply {
    this.text = text
    isAllCaps = false
    setOnClickListener(onClick)
}

fun Context.vstack(vararg children: View): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL
    children.forEach {
        addView(it, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
    }
}

fun Context.hstack(vararg children: View): LinearLayout = LinearLayout(this).apply {
    orientation = LinearLayout.HORIZONTAL
    children.forEach {
        addView(it, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
    }
}

/** A scrolling page around a vertical stack — the Views echo of the UIKit installGroupedForm. */
fun Context.scrollPage(content: LinearLayout): ScrollView = ScrollView(this).apply {
    isFillViewport = true
    addView(content, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
}
