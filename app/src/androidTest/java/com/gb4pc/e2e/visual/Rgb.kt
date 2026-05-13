package com.gb4pc.e2e.visual

data class Rgb(val r: Int, val g: Int, val b: Int) {
    companion object {
        val BLUE   = Rgb(0x15, 0x65, 0xC0)
        val YELLOW = Rgb(0xFF, 0xD6, 0x00)
        val GREEN  = Rgb(0x00, 0xC8, 0x53)
    }
}
