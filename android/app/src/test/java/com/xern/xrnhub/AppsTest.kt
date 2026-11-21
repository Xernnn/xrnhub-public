package com.xern.xrnhub

import com.xern.xrnhub.data.AppGroup
import com.xern.xrnhub.data.AppsDoc
import com.xern.xrnhub.data.Fact
import com.xern.xrnhub.data.HubApp
import com.xern.xrnhub.data.Installed
import com.xern.xrnhub.data.Release
import com.xern.xrnhub.data.appsInOrder
import com.xern.xrnhub.data.compareVersions
import com.xern.xrnhub.data.docJson
import com.xern.xrnhub.data.factsOf
import com.xern.xrnhub.data.holdsWords
import com.xern.xrnhub.data.newerRelease
import com.xern.xrnhub.data.releaseWords
import com.xern.xrnhub.data.serviceWords
import com.xern.xrnhub.data.updateWords
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppsTest {
    private val now = 1_800_000_000.0
    private fun app(vararg released: Release) = HubApp("xrnrule", packageName = "com.xern.xrnrule", released = released.toList())

    /** tests/demo_server.py --fixtures writes this one too: the real hub's answer on a made-up phone. */
    private fun fixture(): AppsDoc = docJson.decodeFromString(AppsDoc.serializer(), javaClass.getResource("/apps.json")!!.readText())

    @Test fun the_hubs_answer_reads_in_full() {
        val doc = fixture()
        assertTrue(doc.owner)
        assertEquals(15, doc.apps.size)
        assertEquals(listOf("time", "life", "focus", "play", "server"), doc.groups.map { it.id })
        val rule = doc.apps.first { it.name == "xrnrule" }
        assertEquals("com.xern.xrnrule", rule.packageName)
        assertEquals(listOf("Phone", "Laptop", "Browser", "Server"), rule.runs)
        assertEquals(Release("phone", "0.2.17", 17), rule.released.first())
        assertEquals("/xrnrule/", rule.web)
        assertEquals(true, rule.up)
        assertTrue(doc.apps.first { it.name == "xrnhome" }.isPublic)
        assertFalse(rule.isPublic)
        assertEquals("check-ins", doc.apps.first { it.name == "xrnloops" }.unit)
        assertNull(doc.apps.first { it.name == "xrnsoc" }.packageName)
    }

    @Test fun the_manifest_names_every_package_the_hub_lists() {
        // Unit tests run in the module's folder. Android 11 on hides a package the manifest doesn't name.
        val manifest = File("src/main/AndroidManifest.xml").readText()
        val named = Regex("""<package android:name="([\w.]+)"""").findAll(manifest).map { it.groupValues[1] }.toSet()
        val listed = fixture().apps.mapNotNull { it.packageName }.toSet() - "com.xern.xrnhub"
        assertEquals(listed, named)
    }

    @Test fun versions_compare_number_by_number() {
        assertEquals(1, compareVersions("0.2.10", "0.2.9"))
        assertEquals(-1, compareVersions("0.2.9", "0.2.10"))
        assertEquals(0, compareVersions("1.1", "1.1.0"))
        assertEquals(1, compareVersions("2.0", "1.99.99"))
        assertEquals(0, compareVersions("0.2.16-debug", "0.2.16"))
        assertEquals(1, compareVersions("1.0.1", ""))
    }

    @Test fun a_higher_code_on_the_server_is_an_update() {
        val release = Release("phone", "0.2.17", 17)
        assertEquals(release, newerRelease(app(release), Installed("0.2.16", 16)))
        assertNull(newerRelease(app(release), Installed("0.2.17", 17)))
        assertNull(newerRelease(app(release), Installed("0.2.18", 18)))
    }

    @Test fun the_code_decides_over_the_name_since_android_does() {
        // Android only installs a higher versionCode over an app, whatever the names say.
        assertNull(newerRelease(app(Release("phone", "0.3.0", 16)), Installed("0.2.16", 16)))
        assertEquals("0.2.16", newerRelease(app(Release("phone", "0.2.16", 17)), Installed("0.2.16", 16))?.version)
    }

    @Test fun without_a_code_the_names_decide() {
        assertEquals("1.2.0", newerRelease(app(Release("phone", "1.2.0")), Installed("1.1.0", 7))?.version)
        assertNull(newerRelease(app(Release("phone", "1.1.0")), Installed("1.1.0", 7)))
        assertEquals("1.2.0", newerRelease(app(Release("phone", "1.2.0", 8)), Installed("1.1.0", 0))?.version)
    }

    @Test fun only_a_phone_release_can_update_the_phone_and_only_if_installed() {
        assertNull(newerRelease(app(Release("extension", "9.9.9")), Installed("1.0", 1)))
        assertNull(newerRelease(app(Release("phone", "9.9.9", 99)), null))
        assertNull(newerRelease(app(), Installed("1.0", 1)))
    }

    @Test fun an_update_is_said_plainly() {
        assertEquals("Update: 0.2.17", updateWords(Release("phone", "0.2.17", 17)))
    }

    @Test fun facts_read_like_the_web_page() {
        val loops = HubApp(
            "xrnloops", service = "xrnloops", packageName = "com.xern.xrnloops", up = true, since = now - 3 * 86400,
            records = 812, unit = "check-ins", week = 54, lastIn = now - 300,
        )
        assertEquals(
            listOf(
                Fact("Server", "xrnloops · Up · 3 days", dot = true, logo = "xrnloops"),
                Fact("Holds", "812 check-ins · 54 this week"),
                Fact("Last sync", "5m ago"),
                Fact("On this phone", "2.3.3"),
            ),
            factsOf(loops, Installed("2.3.3", 20303), now),
        )
        assertEquals("Not installed", factsOf(loops, null, now).last().value)
        assertEquals(emptyList<Fact>(), factsOf(HubApp("xrnsoc"), null, now))
    }

    @Test fun the_server_fact_names_the_service_the_app_talks_to() {
        // xrnfit has none of its own: the hub keeps what it sends, and it's up or down with the hub.
        val fit = HubApp("xrnfit", server = "xrnhub", up = true, since = now - 7200)
        assertEquals(Fact("Server", "xrnhub · Up · 2h", dot = true, logo = "xrnhub"), factsOf(fit, null, now).first())
        val cal = HubApp("xrncal", service = "calendar", server = "calendar", up = false)
        assertEquals(Fact("Server", "xrncal · Down", dot = false, logo = "calendar"), factsOf(cal, null, now).first())
        assertEquals("xrnhub", fixture().apps.first { it.name == "xrnfit" }.server)
        assertNull(fixture().apps.first { it.name == "xrnfit" }.service)
    }

    @Test fun a_service_says_whether_its_up() {
        assertEquals("Not heard from yet", serviceWords(HubApp("a", service = "a"), now))
        assertEquals("Down", serviceWords(HubApp("a", service = "a", up = false, since = now - 60), now))
        assertEquals("Up", serviceWords(HubApp("a", service = "a", up = true), now))
        assertEquals("Up · 2h", serviceWords(HubApp("a", service = "a", up = true, since = now - 7200), now))
    }

    @Test fun holdings_leave_out_a_quiet_week() {
        assertNull(holdsWords(HubApp("a")))
        assertEquals("30 items", holdsWords(HubApp("a", records = 30, unit = "items")))
        assertEquals("30 items", holdsWords(HubApp("a", records = 30, unit = "items", week = 0)))
    }

    @Test fun an_old_backup_reads_as_bad() {
        val server = HubApp("home-server", backup = now - 4 * 86400)
        assertEquals(Fact("Last backup", "4 days ago", bad = true), factsOf(server, null, now).single())
        assertFalse(factsOf(server.copy(backup = now - 3600), null, now).single().bad)
    }

    @Test fun releases_are_named_by_kind() {
        assertEquals("Phone app 0.2.16 · Extension 2.3.6", releaseWords(listOf(Release("phone", "0.2.16", 16), Release("extension", "2.3.6"))))
    }

    @Test fun the_cards_keep_the_groups_order_without_their_headings() {
        val doc = AppsDoc(
            groups = listOf(AppGroup("time"), AppGroup("play")),
            apps = listOf(HubApp("a", group = "play"), HubApp("b", group = "time"), HubApp("c", group = "gone"), HubApp("d", group = "time")),
        )
        assertEquals(listOf("b", "d", "a", "c"), appsInOrder(doc).map { it.name })
        // An answer with no groups at all still lists every app, as it came.
        assertEquals(listOf("a", "b", "c", "d"), appsInOrder(doc.copy(groups = emptyList())).map { it.name })
        assertEquals(fixture().apps.size, appsInOrder(fixture()).size)
    }
}
