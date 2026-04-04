package com.gb4pc.data

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class OverlayPositionStoreTest {

    @Test
    fun `serialize and deserialize roundtrip`() {
        val positions = mapOf(
            "0.45" to OverlayPosition(13.0f, 91.5f, 11.5f),
            "0.56" to OverlayPosition(15.0f, 90.0f, 12.0f)
        )
        val json = OverlayPositionStore.toJson(positions)
        val restored = OverlayPositionStore.fromJson(json)

        assertEquals(2, restored.size)
        assertEquals(13.0f, restored["0.45"]!!.xPercent, 0.001f)
        assertEquals(91.5f, restored["0.45"]!!.yPercent, 0.001f)
        assertEquals(11.5f, restored["0.45"]!!.sizePercent, 0.001f)
        assertEquals(15.0f, restored["0.56"]!!.xPercent, 0.001f)
    }

    @Test
    fun `fromJson returns empty map for empty string`() {
        assertTrue(OverlayPositionStore.fromJson("").isEmpty())
    }

    @Test
    fun `fromJson returns empty map for invalid json`() {
        assertTrue(OverlayPositionStore.fromJson("not json").isEmpty())
    }

    @Test
    fun `toJson produces valid JSON`() {
        val positions = mapOf("0.45" to OverlayPosition(10f, 20f, 5f))
        val json = OverlayPositionStore.toJson(positions)
        // Should not throw
        val parsed = JSONObject(json)
        assertTrue(parsed.has("0.45"))
    }
}
