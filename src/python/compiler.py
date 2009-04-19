from pytools import memoize




@memoize
def get_nvcc_version(nvcc):
    from pytools.prefork import call_capture_stdout
    try:
        return call_capture_stdout([nvcc, "--version"])
    except OSError, e:
        raise OSError, "%s was not found (is it on the PATH?) [%s]" % (
                nvcc, str(e))




def _new_md5(): 
    try:
        import hashlib
        return hashlib.md5()
    except ImportError:
        # for Python << 2.5
        import md5
        return md5.new()




def compile_plain(source, options, keep, nvcc, cache_dir):
    from os.path import join

    if cache_dir:
        checksum = _new_md5()

        checksum.update(source)
        for option in options: 
            checksum.update(option)
        checksum.update(get_nvcc_version(nvcc))

        cache_file = checksum.hexdigest()
        cache_path = join(cache_dir, cache_file + ".cubin")

        try:
            return open(cache_path, "r").read()
        except:
            pass

    from tempfile import mkdtemp
    file_dir = mkdtemp()
    file_root = "kernel"

    cu_file_name = file_root + ".cu"
    cu_file_path = join(file_dir, cu_file_name)

    outf = open(cu_file_path, "w")
    outf.write(str(source))
    outf.close()

    if keep:
        options = options[:]
        options.append("--keep")

        print "*** compiler output in %s" % file_dir

    from pytools.prefork import call
    try:
        result = call([nvcc, "--cubin"]
                + options
                + [cu_file_name],
            cwd=file_dir)
    except OSError, e:
        raise OSError, "%s was not found (is it on the PATH?) [%s]" % (
                nvcc, str(e))

    if result != 0:
        from pycuda.driver import CompileError
        raise CompileError, "nvcc compilation of %s failed" % cu_file_path

    cubin = open(join(file_dir, file_root + ".cubin"), "r").read()

    if cache_dir:
        outf = open(cache_path, "w")
        outf.write(cubin)
        outf.close()

    if not keep:
        from os import listdir, unlink, rmdir
        for name in listdir(file_dir):
            unlink(join(file_dir, name))
        rmdir(file_dir)

    return cubin




def _get_per_user_string():
    try:
        from os import getuid
    except ImportError:
        checksum = _new_md5()
        from os import environ
        checksum.update(environ["HOME"])
        return checksum.hexdigest()
    else:
        return "uid%d" % getuid()




def compile(source, nvcc="nvcc", options=[], keep=False,
        no_extern_c=False, arch=None, code=None, cache_dir=None,
        include_dirs=[]):

    if not no_extern_c:
        source = 'extern "C" {\n%s\n}\n' % source

    options = options[:]
    if arch is None:
        try:
            from pycuda.driver import Context
            arch = "sm_%d%d" % Context.get_device().compute_capability()
        except RuntimeError:
            pass

    if cache_dir is None:
        from os.path import expanduser, join, exists
        import os
        from tempfile import gettempdir
        cache_dir = join(gettempdir(), 
                "pycuda-compiler-cache-v1-%s" % _get_per_user_string())

        if not exists(cache_dir):
            from os import mkdir
            mkdir(cache_dir)

    if arch is not None:
        options.extend(["-arch", arch])

    if code is not None:
        options.extend(["-code", code])

    include_dirs = include_dirs[:]
    from imp import find_module
    file, pathname, descr = find_module("pycuda")
    from os.path import join
    include_dirs.append(join(pathname, "..", "include/cuda"))

    for i in include_dirs:
        options.append("-I"+i)

    return compile_plain(source, options, keep, nvcc, cache_dir)




class SourceModule(object):
    def __init__(self, source, nvcc="nvcc", options=[], keep=False,
            no_extern_c=False, arch=None, code=None, cache_dir=None,
            include_dirs=[]):
        cubin = compile(source, nvcc, options, keep, no_extern_c, 
                arch, code, cache_dir, include_dirs)

        def failsafe_extract(key, cubin):
            pattern = r"%s\s*=\s*([0-9]+)" % key
            import re
            match = re.search(pattern, cubin)
            if match is None:
                from warnings import warn
                warn("Reading '%s' from cubin failed--SourceModule metadata may be unavailable." % key)
                return None
            else:
                return int(match.group(1))

        self.lmem = failsafe_extract("lmem", cubin)
        self.smem = failsafe_extract("smem", cubin)
        self.registers = failsafe_extract("reg", cubin)

        from pycuda.driver import module_from_buffer
        self.module = module_from_buffer(cubin)

        self.get_global = self.module.get_global
        self.get_texref = self.module.get_texref

    def get_function(self, name):
        func = self.module.get_function(name)

        # FIXME: Bzzt, wrong. This should truly be per-function.
        func.lmem = self.lmem
        func.smem = self.smem
        func.registers = self.registers

        return func

