# Builds earthscape-localfs.jar (PureListLocalFileSystem) and installs it on the
# Spark JVM classpath (the jars/ directory of the pyspark distribution used by
# .venv). Required only on this Windows dev box; see the Java source header.
# Usage: powershell -ExecutionPolicy Bypass -File build_localfs.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path "..\..\.."   # -> repo root from data-pipeline/streaming/java
$jdk = "C:\Program Files\Java\jdk-17\bin"
$sparkJars = Join-Path $root ".venv\lib\site-packages\pyspark\jars"
$outDir = Join-Path $root "data-pipeline\streaming\jars"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$classes = Join-Path $env:TEMP "earthscape-localfs-classes"
Remove-Item -Recurse -Force $classes -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $classes | Out-Null
& (Join-Path $jdk "javac.exe") -cp (Join-Path $sparkJars "hadoop-client-api-3.3.4.jar") `
    -d $classes (Join-Path $root "data-pipeline\streaming\java\com\earthscape\hadoop\PureListLocalFileSystem.java")
& (Join-Path $jdk "jar.exe") cf (Join-Path $outDir "earthscape-localfs.jar") -C $classes "com"
Copy-Item (Join-Path $outDir "earthscape-localfs.jar") (Join-Path $sparkJars "earthscape-localfs.jar") -Force
Write-Host "built: $outDir\earthscape-localfs.jar (also copied into pyspark jars/)"
