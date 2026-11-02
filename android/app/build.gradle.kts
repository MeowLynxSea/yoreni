plugins {
    id("com.android.application")
}

android {
    namespace = "dev.yoreni.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.yoreni.app"
        minSdk = 26
        // 28, not 36: Android denies link() in an app's data directory from
        // API 29 on, and dpkg/pip both rely on hardlinks. Termux pins the same
        // value for the same reason.
        targetSdk = 28
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    androidResources {
        // The rootfs is already gzip; aapt would otherwise decompress it and
        // strip the extension, so the asset name the code opens would vanish.
        noCompress += "bin"
    }

    packaging {
        // The rootfs and proot are data, not native libraries.
        resources.excludes += setOf("META-INF/*")
    }
}
