package com.voiceforgaming.vfg.turn;

import static java.lang.foreign.ValueLayout.ADDRESS;
import static java.lang.foreign.ValueLayout.JAVA_BYTE;
import static java.lang.foreign.ValueLayout.JAVA_FLOAT;
import static java.lang.foreign.ValueLayout.JAVA_INT;
import static java.lang.foreign.ValueLayout.JAVA_LONG;

import java.lang.foreign.Arena;
import java.lang.foreign.FunctionDescriptor;
import java.lang.foreign.Linker;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.SymbolLookup;
import java.lang.invoke.MethodHandle;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * ONNX Runtime's C API, called through {@code java.lang.foreign} on the very library
 * sherpa-onnx loads from the natives folder: one runtime in the process, and nothing more
 * to download. Only what the turn models need: a session from a model's bytes on the CPU
 * with one intra-op and one inter-op thread, and float32 tensors in and out by name.
 *
 * <p>ONNX Runtime's own Java API cannot share that library: its JNI library is built against
 * another release of it (on Linux it asks for {@code libonnxruntime.so.1} at symbol version
 * {@code VERS_1.28.0}; sherpa-onnx 1.13.8 ships {@code libonnxruntime.so} 1.28.2). The C API
 * is a table of functions that only ever grows, so each one is at a fixed index in it, the
 * order of {@code struct OrtApi} in {@code onnxruntime_c_api.h}.
 *
 * <p>{@link #open} is normally called after the speech engine has loaded the library;
 * opening the same file again returns the library already loaded.
 */
public final class OnnxRuntime {
	/** Every function used here is in this version's table; ONNX Runtime 1.23 and later serve it. */
	private static final int API_VERSION = 23;

	private static final int GET_ERROR_MESSAGE = 2;
	private static final int CREATE_ENV = 3;
	private static final int CREATE_SESSION_FROM_ARRAY = 8;
	private static final int RUN = 9;
	private static final int CREATE_SESSION_OPTIONS = 10;
	private static final int SET_INTRA_OP_NUM_THREADS = 24;
	private static final int SET_INTER_OP_NUM_THREADS = 25;
	private static final int CREATE_TENSOR_WITH_DATA_AS_ORT_VALUE = 49;
	private static final int CREATE_CPU_MEMORY_INFO = 69;
	private static final int RELEASE_STATUS = 93;
	private static final int RELEASE_SESSION = 95;
	private static final int RELEASE_VALUE = 96;
	private static final int RELEASE_SESSION_OPTIONS = 100;

	private static final int LOGGING_LEVEL_ERROR = 3;
	private static final int ARENA_ALLOCATOR = 1;
	private static final int MEM_TYPE_DEFAULT = 0;
	private static final int TENSOR_FLOAT = 1;

	private static final Linker LINKER = Linker.nativeLinker();
	private static final Map<Path, OnnxRuntime> OPEN = new HashMap<>();

	private final Path library;
	private final String version;
	private final MethodHandle getErrorMessage;
	private final MethodHandle createEnv;
	private final MethodHandle createSessionFromArray;
	private final MethodHandle run;
	private final MethodHandle createSessionOptions;
	private final MethodHandle setIntraOpNumThreads;
	private final MethodHandle setInterOpNumThreads;
	private final MethodHandle createTensor;
	private final MethodHandle createCpuMemoryInfo;
	private final MethodHandle releaseStatus;
	private final MethodHandle releaseSession;
	private final MethodHandle releaseValue;
	private final MethodHandle releaseSessionOptions;
	/**
	 * Never released. ONNX Runtime keeps one environment per process, counted by its users, and
	 * sherpa-onnx's engines share it; holding ours keeps it, and the sessions on it, alive while
	 * a speech engine is closed and another loaded.
	 */
	private MemorySegment env;
	private MemorySegment cpuMemory;

	/** ONNX Runtime could not do what was asked; the message is its own. */
	public static final class OrtException extends RuntimeException {
		public OrtException(String message) {
			super(message);
		}
	}

	/** The library sherpa-onnx loads from {@code nativesDir}: {@code onnxruntime.dll}, {@code libonnxruntime.so} or {@code .dylib}. */
	public static Path library(Path nativesDir) {
		return nativesDir.toAbsolutePath().normalize().resolve(System.mapLibraryName("onnxruntime"));
	}

	/** The ONNX Runtime in {@code nativesDir}, opened once for the process. */
	public static synchronized OnnxRuntime open(Path nativesDir) {
		Path lib = library(nativesDir);
		OnnxRuntime runtime = OPEN.get(lib);
		if (runtime == null) {
			runtime = new OnnxRuntime(lib);
			OPEN.put(lib, runtime);
		}
		return runtime;
	}

	private OnnxRuntime(Path lib) {
		if (!Files.isRegularFile(lib)) {
			throw new IllegalStateException("no ONNX Runtime library at " + lib);
		}
		SymbolLookup symbols;
		try {
			symbols = SymbolLookup.libraryLookup(lib, Arena.global());
		} catch (IllegalArgumentException e) {
			throw new IllegalStateException("could not load " + lib + ": " + e.getMessage(), e);
		}
		MemorySegment getApiBase = symbols.find("OrtGetApiBase")
			.orElseThrow(() -> new IllegalStateException(lib + " is not ONNX Runtime: it has no OrtGetApiBase"));
		// struct OrtApiBase { GetApi; GetVersionString; }
		MemorySegment base = ((MemorySegment) call(LINKER.downcallHandle(getApiBase, FunctionDescriptor.of(ADDRESS))))
			.reinterpret(2 * ADDRESS.byteSize());
		MethodHandle getApi = LINKER.downcallHandle(base.getAtIndex(ADDRESS, 0), FunctionDescriptor.of(ADDRESS, JAVA_INT));
		MethodHandle getVersion = LINKER.downcallHandle(base.getAtIndex(ADDRESS, 1), FunctionDescriptor.of(ADDRESS));
		this.library = lib;
		this.version = string((MemorySegment) call(getVersion));
		MemorySegment api = (MemorySegment) call(getApi, API_VERSION);
		if (api.address() == 0) {
			throw new IllegalStateException("ONNX Runtime " + version + " at " + lib + " does not serve API version "
				+ API_VERSION);
		}
		api = api.reinterpret((RELEASE_SESSION_OPTIONS + 1) * ADDRESS.byteSize());
		getErrorMessage = function(api, GET_ERROR_MESSAGE, FunctionDescriptor.of(ADDRESS, ADDRESS));
		createEnv = function(api, CREATE_ENV, FunctionDescriptor.of(ADDRESS, JAVA_INT, ADDRESS, ADDRESS));
		createSessionFromArray = function(api, CREATE_SESSION_FROM_ARRAY,
			FunctionDescriptor.of(ADDRESS, ADDRESS, ADDRESS, JAVA_LONG, ADDRESS, ADDRESS));
		run = function(api, RUN,
			FunctionDescriptor.of(ADDRESS, ADDRESS, ADDRESS, ADDRESS, ADDRESS, JAVA_LONG, ADDRESS, JAVA_LONG, ADDRESS));
		createSessionOptions = function(api, CREATE_SESSION_OPTIONS, FunctionDescriptor.of(ADDRESS, ADDRESS));
		setIntraOpNumThreads = function(api, SET_INTRA_OP_NUM_THREADS, FunctionDescriptor.of(ADDRESS, ADDRESS, JAVA_INT));
		setInterOpNumThreads = function(api, SET_INTER_OP_NUM_THREADS, FunctionDescriptor.of(ADDRESS, ADDRESS, JAVA_INT));
		createTensor = function(api, CREATE_TENSOR_WITH_DATA_AS_ORT_VALUE,
			FunctionDescriptor.of(ADDRESS, ADDRESS, ADDRESS, JAVA_LONG, ADDRESS, JAVA_LONG, JAVA_INT, ADDRESS));
		createCpuMemoryInfo = function(api, CREATE_CPU_MEMORY_INFO, FunctionDescriptor.of(ADDRESS, JAVA_INT, JAVA_INT, ADDRESS));
		releaseStatus = function(api, RELEASE_STATUS, FunctionDescriptor.ofVoid(ADDRESS));
		releaseSession = function(api, RELEASE_SESSION, FunctionDescriptor.ofVoid(ADDRESS));
		releaseValue = function(api, RELEASE_VALUE, FunctionDescriptor.ofVoid(ADDRESS));
		releaseSessionOptions = function(api, RELEASE_SESSION_OPTIONS, FunctionDescriptor.ofVoid(ADDRESS));
	}

	/** The library's file. */
	public Path library() {
		return library;
	}

	/** Its version, as it reports it: {@code 1.28.2} for sherpa-onnx 1.13.8's. */
	public String version() {
		return version;
	}

	/**
	 * A session for an ONNX model, from its bytes rather than its path (which on Windows
	 * would have to be a wide string). CPU only, one intra-op and one inter-op thread.
	 */
	public Session session(byte[] model) {
		MemorySegment environment = environment();
		try (Arena arena = Arena.ofConfined()) {
			MemorySegment out = arena.allocate(ADDRESS);
			check(call(createSessionOptions, out));
			MemorySegment options = out.get(ADDRESS, 0);
			try {
				check(call(setIntraOpNumThreads, options, 1));
				check(call(setInterOpNumThreads, options, 1));
				MemorySegment bytes = arena.allocate(model.length);
				MemorySegment.copy(model, 0, bytes, JAVA_BYTE, 0, model.length);
				check(call(createSessionFromArray, environment, bytes, (long) model.length, options, out));
				return new Session(out.get(ADDRESS, 0));
			} finally {
				call(releaseSessionOptions, options);
			}
		}
	}

	private synchronized MemorySegment environment() {
		if (env == null) {
			try (Arena arena = Arena.ofConfined()) {
				MemorySegment out = arena.allocate(ADDRESS);
				check(call(createCpuMemoryInfo, ARENA_ALLOCATOR, MEM_TYPE_DEFAULT, out));
				cpuMemory = out.get(ADDRESS, 0);
				check(call(createEnv, LOGGING_LEVEL_ERROR, arena.allocateFrom("vfg"), out));
				env = out.get(ADDRESS, 0);
			}
		}
		return env;
	}

	/**
	 * One model's session and the tensors it runs on, which live in native memory as long as
	 * it does. Not for two threads at once: its user holds a lock around it.
	 */
	public final class Session implements AutoCloseable {
		private final Arena arena = Arena.ofShared();
		private final List<Tensor> tensors = new ArrayList<>();
		private MemorySegment session;

		private Session(MemorySegment session) {
			this.session = session;
		}

		/** A float32 tensor of this shape, all zeros, for this session's inputs and outputs. */
		public Tensor tensor(long... shape) {
			live();
			long size = 1;
			for (long d : shape) {
				size *= d;
			}
			if (size <= 0 || size > Integer.MAX_VALUE) {
				throw new IllegalArgumentException("shape " + Arrays.toString(shape));
			}
			MemorySegment data = arena.allocate(size * Float.BYTES, 64);
			try (Arena scratch = Arena.ofConfined()) {
				MemorySegment out = scratch.allocate(ADDRESS);
				check(call(createTensor, cpuMemory, data, size * Float.BYTES, scratch.allocateFrom(JAVA_LONG, shape),
					(long) shape.length, TENSOR_FLOAT, out));
				Tensor t = new Tensor(this, (int) size, data, out.get(ADDRESS, 0));
				tensors.add(t);
				return t;
			}
		}

		/**
		 * What one run reads and writes: each input tensor by its name, and each named output
		 * written into its tensor, which must have the output's shape. A tensor is not both an
		 * input and an output. Laid out once, in the session's memory, for every run that uses it.
		 */
		public Binding binding(Map<String, Tensor> inputs, Map<String, Tensor> outputs) {
			live();
			for (Map.Entry<String, Tensor> e : outputs.entrySet()) {
				if (inputs.containsValue(e.getValue())) {
					throw new IllegalArgumentException(e.getKey() + ": a tensor is not both an input and an output");
				}
			}
			return new Binding(this, names(inputs), values(inputs), inputs.size(), names(outputs), values(outputs),
				outputs.size());
		}

		private MemorySegment names(Map<String, Tensor> tensors) {
			MemorySegment names = arena.allocate(ADDRESS, tensors.size());
			int i = 0;
			for (String name : tensors.keySet()) {
				names.setAtIndex(ADDRESS, i++, arena.allocateFrom(name));
			}
			return names;
		}

		private MemorySegment values(Map<String, Tensor> tensors) {
			MemorySegment values = arena.allocate(ADDRESS, tensors.size());
			int i = 0;
			for (Tensor t : tensors.values()) {
				values.setAtIndex(ADDRESS, i++, own(t).value);
			}
			return values;
		}

		/** Runs the model on a binding of this session. */
		public void run(Binding b) {
			live();
			if (b.session != this) {
				throw new IllegalArgumentException("a binding of another session");
			}
			MemorySegment status;
			try {
				status = (MemorySegment) run.invokeExact(session, MemorySegment.NULL, b.inNames, b.inValues, b.inputs,
					b.outNames, b.outputs, b.outValues);
			} catch (Throwable t) {
				throw rethrow(t);
			}
			check(status);
		}

		private Tensor own(Tensor t) {
			if (t.session != this) {
				throw new IllegalArgumentException("a tensor of another session");
			}
			return t;
		}

		private void live() {
			if (session == null) {
				throw new IllegalStateException("ONNX Runtime session closed");
			}
		}

		/** Releases the session and its tensors. */
		@Override
		public void close() {
			if (session == null) {
				return;
			}
			for (Tensor t : tensors) {
				call(releaseValue, t.value);
			}
			tensors.clear();
			call(releaseSession, session);
			session = null;
			arena.close();
		}
	}

	/** The names and {@code OrtValue}s of one run's inputs and outputs, as {@code Run} takes them. */
	public static final class Binding {
		private final Session session;
		private final MemorySegment inNames;
		private final MemorySegment inValues;
		private final long inputs;
		private final MemorySegment outNames;
		private final MemorySegment outValues;
		private final long outputs;

		private Binding(Session session, MemorySegment inNames, MemorySegment inValues, long inputs, MemorySegment outNames,
				MemorySegment outValues, long outputs) {
			this.session = session;
			this.inNames = inNames;
			this.inValues = inValues;
			this.inputs = inputs;
			this.outNames = outNames;
			this.outValues = outValues;
			this.outputs = outputs;
		}
	}

	/** A float32 tensor in native memory that ONNX Runtime reads from or writes into in place. */
	public static final class Tensor {
		private final Session session;
		private final int size;
		private final MemorySegment data;
		private final MemorySegment value;

		private Tensor(Session session, int size, MemorySegment data, MemorySegment value) {
			this.session = session;
			this.size = size;
			this.data = data;
			this.value = value;
		}

		/** Copies in {@code from}, which holds exactly as many floats as the tensor. */
		public void set(float[] from) {
			if (from.length != size) {
				throw new IllegalArgumentException(from.length + " floats for a tensor of " + size);
			}
			MemorySegment.copy(from, 0, data, JAVA_FLOAT, 0, size);
		}

		/** One float, in row-major order. */
		public float get(int index) {
			return data.getAtIndex(JAVA_FLOAT, Objects.checkIndex(index, size));
		}

		public void zero() {
			data.fill((byte) 0);
		}
	}

	// --- calling into the table ---

	private static MethodHandle function(MemorySegment api, int index, FunctionDescriptor descriptor) {
		return LINKER.downcallHandle(api.getAtIndex(ADDRESS, index), descriptor);
	}

	/** For the calls made at load and close; {@link Session#run} calls {@code Run} with {@code invokeExact}. */
	private static Object call(MethodHandle function, Object... args) {
		try {
			return function.invokeWithArguments(args);
		} catch (Throwable t) {
			throw rethrow(t);
		}
	}

	private static RuntimeException rethrow(Throwable t) {
		if (t instanceof RuntimeException e) {
			return e;
		}
		if (t instanceof Error e) {
			throw e;
		}
		return new IllegalStateException(t);
	}

	/** A non-null {@code OrtStatus*} becomes an {@link OrtException} with its message, and is released. */
	private void check(Object status) {
		MemorySegment s = (MemorySegment) status;
		if (s.address() == 0) {
			return;
		}
		String message;
		try {
			message = string((MemorySegment) call(getErrorMessage, s));
		} finally {
			call(releaseStatus, s);
		}
		throw new OrtException(message);
	}

	private static String string(MemorySegment cString) {
		return cString.reinterpret(Long.MAX_VALUE).getString(0);
	}
}
