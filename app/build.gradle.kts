import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "app.orchard.tv"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.orchard.tv"
        // Android 5.0. Compose 1.10 and later need 6.0, so the Compose BOM below stays on 1.9.
        minSdk = 21
        targetSdk = 34
        versionCode = 90
        versionName = "1.72"
        // `./gradlew -Pdiag ...`: a build for one reporter's TV whose logcat shows no app lines;
        // see system/DiagLog.kt.
        val diag = project.hasProperty("diag")
        buildConfigField("boolean", "DIAG", diag.toString())
        if (diag) versionNameSuffix = "-diag"

        // Household secret, injected the same way as the signing key: the admin phrase's hash
        // lives in local/admin.properties (gitignored, see AGENTS.md). A checkout without that
        // file has an empty hash, which simply disables the admin recovery path.
        val adminProps = rootProject.file("local/admin.properties")
        val adminHash = if (adminProps.exists()) {
            Properties().apply { adminProps.inputStream().use { load(it) } }
                .getProperty("adminHash") ?: ""
        } else ""
        buildConfigField("String", "ADMIN_HASH", "\"$adminHash\"")

        // The quiz server (docs/QUIZ.md): its address, the ES256 key it signs tickets with, and a
        // development HMAC key for a build with no server certificate yet. All three come from
        // local/quiz.properties (gitignored, see AGENTS.md), so the address and the key are decided
        // by whoever deploys the server and cannot be edited on the television.
        //
        // A checkout without that file gets three empty strings: the quiz page then says the server
        // is not configured and everything else works as before. That is on purpose — a client that
        // falls back to "trust anything" when its pin is missing is worse than one that does not run.
        val quizProps = rootProject.file("local/quiz.properties")
        val quiz = Properties().apply {
            if (quizProps.exists()) quizProps.inputStream().use { load(it) }
        }
        buildConfigField("String", "QUIZ_BASE_URL", "\"${quiz.getProperty("baseUrl", "").trim()}\"")
        buildConfigField("String", "QUIZ_PUBKEY", "\"${quiz.getProperty("publicKey", "").trim()}\"")
        buildConfigField("String", "QUIZ_DEV_HMAC", "\"${quiz.getProperty("devHmac", "").trim()}\"")
    }

    // standard: a normal HOME app. guard: no HOME intent filter at all, for China-market firmwares
    // (some TCL / Hisense builds) that refuse to install any package declaring CATEGORY_HOME. The guard
    // build takes over Home through its accessibility service instead.
    flavorDimensions += "home"
    productFlavors {
        create("standard") { dimension = "home" }
        create("guard") {
            dimension = "home"
            versionNameSuffix = "-guard"
        }
    }

    // The TVs only accept updates signed with the key Aerial was first installed with. That key
    // lives outside version control in local/ (see docs/HANDOFF.md); without it the build falls
    // back to this machine's debug key, which installs fresh but cannot update existing copies.
    val signingProps = rootProject.file("local/signing.properties")
    signingConfigs {
        if (signingProps.exists()) {
            val p = Properties().apply { signingProps.inputStream().use { load(it) } }
            create("aerial") {
                storeFile = rootProject.file("local/" + p.getProperty("storeFile"))
                storePassword = p.getProperty("storePassword")
                keyAlias = p.getProperty("keyAlias")
                keyPassword = p.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("aerial") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        // java.time and friends on Android 5.x to 7.x.
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests.isIncludeAndroidResources = true
    }
    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "DebugProbesKt.bin", "kotlin-tooling-metadata.json")
    }
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.17")
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.5")
    // Compose 1.9.5, the last release that runs on Android 5.0 (1.10 raised minSdk to 23).
    val composeBom = platform("androidx.compose:compose-bom:2025.11.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.animation:animation")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.2")
    implementation("androidx.core:core-ktx:1.16.0")
    // Baseline Profile install (src/main/baseline-prof.txt) and its compile state in the diagnostics.
    implementation("androidx.profileinstaller:profileinstaller:1.4.0")
    // Aerial films. 1.8 is the last Media3 that runs on Android 5.0 (1.9 raised minSdk to 23).
    implementation("androidx.media3:media3-exoplayer:1.8.1")
    implementation("androidx.media3:media3-datasource:1.8.1")
    implementation("androidx.media3:media3-database:1.8.1")
    // Phone setup page (a tiny LAN web server) and the QR code that opens it.
    implementation("org.nanohttpd:nanohttpd:2.3.1")
    implementation("com.google.zxing:core:3.5.3")
}
