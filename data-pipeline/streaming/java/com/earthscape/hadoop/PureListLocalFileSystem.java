package com.earthscape.hadoop;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

import org.apache.hadoop.fs.FileStatus;
import org.apache.hadoop.fs.Path;
import org.apache.hadoop.fs.RawLocalFileSystem;
import org.apache.hadoop.fs.permission.FsPermission;

/**
 * Drop-in replacement for Hadoop's RawLocalFileSystem used ONLY on this
 * Windows development box (registered via spark.hadoop.fs.file.impl in
 * data-pipeline/spark/spark_session.py).
 *
 * Why: without winutils.exe / hadoop.dll (not present here, and binary
 * downloads are not permitted), three RawLocalFileSystem operations call
 * native/shell helpers and crash the JVM:
 *   1. listStatus()        -> FileUtil.canRead -> NativeIO.access0
 *   2. mkdirs(path, mode)  -> mkOneDirWithMode -> Shell.getSetPermissionCommand
 *   3. setPermission()     -> NativeIO.changeMode
 * Each is overridden below with the pure-Java equivalent. On Windows NTFS the
 * POSIX mode bits are not representable anyway (stock Hadoop only best-effort
 * maps them), so skipping chmod has no effect on the data, layout or read/
 * write semantics of this pipeline; every other operation is inherited
 * unchanged. On a real cluster the scheme is hdfs:// and this class is never
 * used.
 *
 * Build (JDK 17 javac/jar against the jars shipped with pyspark):
 *   see data-pipeline/streaming/java/build_localfs.ps1
 */
public class PureListLocalFileSystem extends RawLocalFileSystem {

    @Override
    public FileStatus[] listStatus(Path f) throws IOException {
        File dir = pathToFile(f);
        if (!dir.exists()) {
            throw new FileNotFoundException("File " + f + " does not exist");
        }
        if (dir.isFile()) {
            return new FileStatus[] { getFileStatus(f) };
        }
        File[] children = dir.listFiles();
        if (children == null) {
            throw new FileNotFoundException("Not a readable directory: " + f);
        }
        List<FileStatus> statuses = new ArrayList<>(children.length);
        for (File child : children) {
            try {
                statuses.add(getFileStatus(new Path(f, child.getName())));
            } catch (FileNotFoundException ignored) {
                // Child raced away between enumeration and stat; upstream
                // RawLocalFileSystem skips it the same way.
            }
        }
        return statuses.toArray(new FileStatus[0]);
    }

    @Override
    public boolean mkdirs(Path f) throws IOException {
        return new File(pathToFile(f).getAbsolutePath()).mkdirs()
                || getFileStatus(f).isDirectory();
    }

    @Override
    public boolean mkdirs(Path f, FsPermission permission) throws IOException {
        // POSIX mode is not representable on NTFS; create the tree with the
        // inherited ACLs instead of invoking chmod through winutils.
        return mkdirs(f);
    }

    @Override
    public void setPermission(Path f, FsPermission permission) throws IOException {
        // No-op on this local Windows shim (see class header); never used for
        // hdfs:// cluster runs.
    }
}
