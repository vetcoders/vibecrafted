/* A real product-named Mach-O/ELF owns embedded CPython; no python child or
 * argv[0] disguise. The existing release closure signs this host and its
 * pinned generation-local library. Py_BytesMain is Python's stable ABI:
 * https://docs.python.org/3.12/c-api/init.html#c.Py_BytesMain
 */
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <wchar.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
/* Real symbol references keep libpython in LC_LOAD_DYLIB and in the closed
 * product dependency graph. Linux retains its generation-local dlopen door. */
extern int Py_BytesMain(int, char **);
extern wchar_t *Py_DecodeLocale(const char *, size_t *);
extern void Py_SetPythonHome(const wchar_t *);
extern void Py_SetPath(const wchar_t *);
#endif

#ifndef VC_PYTHON_LIB
#error VC_PYTHON_LIB must name the pinned payload library
#endif
#ifndef VC_ENTRYPOINT
#error VC_ENTRYPOINT must name the canonical Python entrypoint
#endif

static int join(char *buffer, size_t size, const char *root, const char *suffix) {
    int n = snprintf(buffer, size, "%s/%s", root, suffix);
    return n >= 0 && (size_t)n < size;
}

static int extract_stdlib_name(char *dest, size_t dest_size, const char *lib_name) {
    const char *p = strstr(lib_name, "python");
    if (!p) {
        p = strstr(lib_name, "Python");
    }
    if (!p) return 0;
    const char *start = p;
    p += 6; /* skip "python" or "Python" */
    if (*p < '0' || *p > '9') return 0;
    while (*p >= '0' && *p <= '9') p++;
    if (*p != '.') return 0;
    p++;
    if (*p < '0' || *p > '9') return 0;
    while (*p >= '0' && *p <= '9') p++;
    if (*p == 't') p++;
    size_t len = (size_t)(p - start);
    if (len + 1 > dest_size) return 0;
    int n = snprintf(dest, dest_size, "python%.*s", (int)(len - 6), start + 6);
    return n > 0 && (size_t)n < dest_size;
}

static int run_python(int (*python_main)(int, char **), int argc, char **argv) {
    char **sealed_argv = calloc((size_t)argc + 2, sizeof(char *));
    if (!sealed_argv) return 71;
    sealed_argv[0] = argv[0];
    sealed_argv[1] = "-B";
    for (int i = 1; i < argc; ++i) sealed_argv[i + 1] = argv[i];
    int result = python_main(argc + 1, sealed_argv);
    free(sealed_argv);
    return result;
}

int main(int argc, char **argv) {
    char executable[PATH_MAX], raw[PATH_MAX], root[PATH_MAX];
#ifdef __APPLE__
    uint32_t size = sizeof(raw);
    if (_NSGetExecutablePath(raw, &size) != 0) return 78;
#else
    ssize_t size = readlink("/proc/self/exe", raw, sizeof(raw) - 1);
    if (size < 0 || (size_t)size >= sizeof(raw) - 1) return 78;
    raw[size] = '\0';
#endif
    if (!realpath(raw, executable)) return 78;
    int copied = snprintf(root, sizeof(root), "%s", executable);
    if (copied < 0 || (size_t)copied >= sizeof(root)) return 78;
    char *slash = strrchr(root, '/');
    if (!slash) return 78;
    *slash = '\0';
    slash = strrchr(root, '/');
    if (!slash) return 78;
    *slash = '\0';
    char library[PATH_MAX], home[PATH_MAX], python_lib[PATH_MAX];
    if (!join(library, sizeof(library), root, "python/lib/" VC_PYTHON_LIB) ||
        !join(home, sizeof(home), root, "python") ||
        !join(python_lib, sizeof(python_lib), root, "python/lib")) return 78;

    char stdlib_name[64];
    if (!extract_stdlib_name(stdlib_name, sizeof(stdlib_name), VC_PYTHON_LIB)) return 78;

    char stdlib[PATH_MAX], dynload[PATH_MAX], site_pkgs[PATH_MAX];
    if (!join(stdlib, sizeof(stdlib), python_lib, stdlib_name) ||
        !join(dynload, sizeof(dynload), stdlib, "lib-dynload") ||
        !join(site_pkgs, sizeof(site_pkgs), stdlib, "site-packages")) return 78;

    struct stat st;
    if (stat(stdlib, &st) != 0 || !S_ISDIR(st.st_mode)) {
        fprintf(stderr, "vc native host: stdlib unavailable in this generation\n");
        return 78;
    }

    int isolated = 0;
#ifndef VC_PYTHON_ONLY
    int has_c_or_m = 0;
#endif
    for (int i = 1; i < argc; ++i) {
        const char *arg = argv[i];
        if (arg[0] != '-') break;
        if (arg[1] == '\0') break; /* "-" reads from stdin, terminates options */
        if (strcmp(arg, "--") == 0) break; /* "--" terminates options */
        if (arg[1] == '-') {
            if (strncmp(arg, "--check-hash-based-pycs=", 24) == 0) {
                continue;
            } else if (strcmp(arg, "--check-hash-based-pycs") == 0) {
                if (i + 1 < argc) i++;
                continue;
            }
            continue;
        }
        const char *p = arg + 1;
        while (*p) {
            char opt = *p;
            if (opt == 'I' || opt == 'E') {
                isolated = 1;
                p++;
            } else if (opt == 'c' || opt == 'm') {
#ifndef VC_PYTHON_ONLY
                has_c_or_m = 1;
#endif
                goto done_options;
            } else if (opt == 'W' || opt == 'X') {
                p++;
                if (*p == '\0') {
                    if (i + 1 < argc) i++;
                }
                break;
            } else {
                p++;
            }
        }
    }
done_options: ;

    char search_path[PATH_MAX * 10];
    const char *env_pythonpath = getenv("PYTHONPATH");
    int n;
    if (!isolated && env_pythonpath && env_pythonpath[0]) {
        n = snprintf(search_path, sizeof(search_path),
                     "%s/vibecrafted-core:%s/vibecrafted-mcp:%s/python-site:%s:%s:%s:%s",
                     root, root, root, stdlib, dynload, site_pkgs, env_pythonpath);
    } else {
        n = snprintf(search_path, sizeof(search_path),
                     "%s/vibecrafted-core:%s/vibecrafted-mcp:%s/python-site:%s:%s:%s",
                     root, root, root, stdlib, dynload, site_pkgs);
    }
    if (n < 0 || (size_t)n >= sizeof(search_path)) return 78;
    unsetenv("__PYVENV_LAUNCHER__");
    if (setenv("PYTHONNOUSERSITE", "1", 1) || setenv("PYTHONDONTWRITEBYTECODE", "1", 1) ||
        setenv("VIBECRAFTED_RUNTIME_ROOT", root, 1)) return 78;
#ifdef __APPLE__
    Dl_info image;
    char expected_library[PATH_MAX], loaded_library[PATH_MAX];
    if (!realpath(library, expected_library) ||
        !dladdr((const void *)Py_BytesMain, &image) || !image.dli_fname ||
        !realpath(image.dli_fname, loaded_library) ||
        strcmp(expected_library, loaded_library)) {
        fprintf(stderr, "vc native host: CPython must load from this generation\n");
        return 78;
    }
    int (*python_main)(int, char **) = Py_BytesMain;
    wchar_t *(*decode_locale)(const char *, size_t *) = Py_DecodeLocale;
    void (*set_home)(const wchar_t *) = Py_SetPythonHome;
    void (*set_path)(const wchar_t *) = Py_SetPath;
#else
    void *handle = dlopen(library, RTLD_NOW | RTLD_GLOBAL);
    if (!handle) {
        fprintf(stderr, "vc native host: pinned CPython library unavailable: %s\n", dlerror());
        return 78;
    }
    int (*python_main)(int, char **) = (int (*)(int, char **))dlsym(handle, "Py_BytesMain");
    if (!python_main) return 78;
    /* The stable ABI keeps home programmatic: -I/-E intentionally
     * ignore PYTHONHOME, but must still find this closed generation's stdlib. */
    wchar_t *(*decode_locale)(const char *, size_t *) =
        (wchar_t *(*)(const char *, size_t *))dlsym(handle, "Py_DecodeLocale");
    void (*set_home)(const wchar_t *) =
        (void (*)(const wchar_t *))dlsym(handle, "Py_SetPythonHome");
    void (*set_path)(const wchar_t *) =
        (void (*)(const wchar_t *))dlsym(handle, "Py_SetPath");
    if (!decode_locale || !set_home || !set_path) return 78;
#endif
    wchar_t *python_home = decode_locale(home, NULL);
    if (!python_home) return 71;
    set_home(python_home); /* Retained for the complete interpreter lifetime. */
    wchar_t *python_path = decode_locale(search_path, NULL);
    if (!python_path) return 71;
    set_path(python_path);
#ifdef VC_PYTHON_ONLY
    return run_python(python_main, argc, argv);
#else
    /* Internal core callers reuse sys.executable for Python module helpers.
     * Retain that actual interpreter contract within the named host. */
    if (has_c_or_m) {
        return run_python(python_main, argc, argv);
    }
    char **python_argv = calloc((size_t)argc + 5, sizeof(char *));
    if (!python_argv) return 71;
    python_argv[0] = executable;
    python_argv[1] = "-c";
    python_argv[2] = "from importlib import import_module; import sys; target=sys.argv.pop(1); sys.argv[0]=sys.argv.pop(1); module,attr=target.split(':',1); raise SystemExit(getattr(import_module(module),attr)())";
    python_argv[3] = VC_ENTRYPOINT;
    python_argv[4] = executable;
    for (int i = 1; i < argc; ++i) python_argv[i + 4] = argv[i];
    int result = run_python(python_main, argc + 4, python_argv);
    free(python_argv);
    return result;
#endif
}
