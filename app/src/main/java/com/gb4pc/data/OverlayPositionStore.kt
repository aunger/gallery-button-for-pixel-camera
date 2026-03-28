package com.gb4pc.data

import org.json.JSONObject

/**
 * Serializes/deserializes overlay positions to/from JSON (DA-02).
 */
object OverlayPositionStore {

    fun toJson(positions: Map<String, OverlayPosition>): String {
        val root = JSONObject()
        for ((ratio, pos) in positions) {
            val obj = JSONObject().apply {
                put("x", pos.xPercent.toDouble())
                put("y", pos.yPercent.toDouble())
                put("size", pos.sizePercent.toDouble())
            }
            root.put(ratio, obj)
        }
        return root.toString()
    }

    fun fromJson(json: String): Map<String, OverlayPosition> {
        if (json.isBlank()) return emptyMap()
        return try {
            val root = JSONObject(json)
            val result = mutableMapOf<String, OverlayPosition>()
            for (key in root.keys()) {
                val obj = root.getJSONObject(key)
                result[key] = OverlayPosition(
                    xPercent = obj.getDouble("x").toFloat(),
                    yPercent = obj.getDouble("y").toFloat(),
                    sizePercent = obj.getDouble("size").toFloat()
                )
            }
            result
        } catch (e: Exception) {
            emptyMap()
        }
    }
}
