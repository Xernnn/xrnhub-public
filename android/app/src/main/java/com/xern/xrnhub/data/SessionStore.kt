package com.xern.xrnhub.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private const val RENEW_AFTER_MS = 7 * 86_400_000L // sessions last 30 days

data class Session(val address: String, val user: String, val cookie: String, val madeAt: Long) {
    fun isOld(nowMs: Long): Boolean = nowMs - madeAt >= RENEW_AFTER_MS
}

/**
 * What a renewal that started from `held` should leave: the new session, or null when the sign-in it
 * started from has gone meanwhile (signed out, or signed in again), which a late answer mustn't undo.
 */
fun renewed(current: Session?, held: Session, cookie: String, nowMs: Long): Session? =
    current?.takeIf { it == held }?.copy(cookie = cookie, madeAt = nowMs)

/** Where this phone keeps its sign-in. The password itself is never stored. */
class SessionStore(context: Context) {
    private val prefs = context.getSharedPreferences("session", Context.MODE_PRIVATE)

    val lastAddress: String? get() = prefs.getString(ADDRESS, null)
    val lastUser: String? get() = prefs.getString(USER, null)

    fun load(): Session? {
        val address = lastAddress ?: return null
        val user = lastUser ?: return null
        val sealed = prefs.getString(COOKIE, null) ?: return null
        val cookie = runCatching { Sealed.open(sealed) }.getOrNull() ?: return null
        return Session(address, user, cookie, prefs.getLong(MADE_AT, 0))
    }

    fun save(session: Session) = synchronized(LOCK) {
        prefs.edit()
            .putString(ADDRESS, session.address)
            .putString(USER, session.user)
            .putString(COOKIE, Sealed.seal(session.cookie))
            .putLong(MADE_AT, session.madeAt)
            .apply()
    }

    /** Keeps the address and name, so signing back in is only the password. */
    fun forgetCookie() = synchronized(LOCK) {
        prefs.edit().remove(COOKIE).remove(MADE_AT).apply()
    }

    /** Keeps a renewal's cookie, unless the sign-in it renewed has gone meanwhile. Returns what it kept. */
    fun renew(held: Session, cookie: String, nowMs: Long): Session? = synchronized(LOCK) {
        renewed(load(), held, cookie, nowMs)?.also(::save)
    }

    private companion object {
        val LOCK = Any() // the app and the background check each have a store over the same file
        const val ADDRESS = "address"
        const val USER = "user"
        const val COOKIE = "cookie"
        const val MADE_AT = "madeAt"
    }
}

/** Encrypts with a key that stays inside the phone's keystore, so the file alone is no use. */
private object Sealed {
    private const val KEYSTORE = "AndroidKeyStore"
    private const val ALIAS = "xrnhub-session"
    private const val CIPHER = "AES/GCM/NoPadding"
    private const val IV_BYTES = 12
    private const val TAG_BITS = 128

    private fun key(): SecretKey {
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return generator.generateKey()
    }

    fun seal(text: String): String {
        val cipher = Cipher.getInstance(CIPHER).apply { init(Cipher.ENCRYPT_MODE, key()) }
        return Base64.getEncoder().encodeToString(cipher.iv + cipher.doFinal(text.toByteArray()))
    }

    fun open(sealed: String): String {
        val bytes = Base64.getDecoder().decode(sealed)
        val spec = GCMParameterSpec(TAG_BITS, bytes, 0, IV_BYTES)
        val cipher = Cipher.getInstance(CIPHER).apply { init(Cipher.DECRYPT_MODE, key(), spec) }
        return String(cipher.doFinal(bytes, IV_BYTES, bytes.size - IV_BYTES))
    }
}
