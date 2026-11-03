#![allow(non_snake_case)]
#![allow(non_camel_case_types)]
#![allow(dead_code)]

//! In-process CPU sampler for Stage E3.
//!
//! Why this exists: on this box the session is not elevated, so the ETW kernel
//! logger (`wpr -start CPU`) is refused with 0xc5585011, and perf/VTune/samply
//! are unavailable. We therefore sample from *inside* the process: a dedicated
//! thread periodically suspends every other thread, reads its CONTEXT, walks the
//! stack with DbgHelp, and records the captured instruction pointers.
//!
//! This is genuine fixed-tick statistical sampling of where threads execute -- no
//! code is added to the proving path -- gated behind `ORCHARD_E3_SAMPLE`.
//!
//! Measurement model (important for interpreting the output):
//!   * Each tick samples every non-sampler thread once, so a thread's sample count
//!     is proportional to the *wall-clock time that thread existed*, and -- once
//!     idle ticks are removed -- to its **CPU (thread) time**.
//!   * A sample whose leaf frame is a wait/sleep primitive is classified as IDLE.
//!     Percentages in `hot_functions.csv` are therefore computed over BUSY samples
//!     only, which is the correct denominator for CPU-cost attribution. The
//!     absolute idle/busy split is reported per thread in `threads.csv`, which is
//!     how parallel utilisation is derived (see the E3 document).

use std::collections::HashMap;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::mem::size_of;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::Diagnostics::Debug::{
    CONTEXT, GetThreadContext, IMAGEHLP_LINEW64, IMAGEHLP_MODULEW64, STACKFRAME64, SYMBOL_INFOW,
    SymCleanup, SymFromAddrW, SymFunctionTableAccess64, SymGetLineFromAddrW64, SymGetModuleBase64,
    SymGetModuleInfoW64, SymInitializeW, SymSetOptions, StackWalk64,
};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, MODULEENTRY32W, Module32FirstW, Module32NextW, TH32CS_SNAPMODULE,
    TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First, Thread32Next,
};
use windows_sys::Win32::System::Threading::{
    GetCurrentProcess, GetCurrentProcessId, GetCurrentThreadId, OpenThread, ResumeThread,
    SuspendThread, THREAD_GET_CONTEXT, THREAD_SUSPEND_RESUME,
};

const MAX_FRAMES: usize = 32;
const SYM_NAME_LEN: usize = 1024;
/// Refreshing the thread list costs a full system thread enumeration, so it is
/// cached and refreshed only every `TID_REFRESH`.
const TID_REFRESH: Duration = Duration::from_millis(500);

/// Leaf frames matching these are treated as "idle" (blocked, not consuming CPU).
const IDLE_PATTERNS: &[&str] = &[
    "NtWaitForWorkViaWorkerFactory",
    "ZwWaitForWorkViaWorkerFactory",
    "NtWaitForAlertByThreadId",
    "ZwWaitForAlertByThreadId",
    "RtlWaitOnAddress",
    "NtDelayExecution",
    "ZwDelayExecution",
    "NtWaitForSingleObject",
    "ZwWaitForSingleObject",
    "WaitForSingleObject",
    "WaitForMultipleObjects",
    "NtRemoveIoCompletion",
    "ZwRemoveIoCompletion",
    "NtWaitForMultipleObjects",
    "ZwWaitForMultipleObjects",
    "NtTestAlert",
    "KeWaitForSingleObject",
    "Sleep",
];

pub struct Sampler {
    stop: Arc<AtomicBool>,
    samples: Arc<Mutex<Vec<(u32, u8, Vec<u64>)>>>,
    handle: Option<thread::JoinHandle<()>>,
    interval_ms: u64,
}

impl Sampler {
    /// Start the sampler. `interval_ms` is the wall-clock sampling period.
    pub fn start(interval_ms: u64) -> Sampler {
        let stop = Arc::new(AtomicBool::new(false));
        let samples: Arc<Mutex<Vec<(u32, u8, Vec<u64>)>>> = Arc::new(Mutex::new(Vec::new()));
        let samples_t = samples.clone();
        let stop_t = stop.clone();

        // DbgHelp symbol search path: the directory of the running exe plus its
        // `deps` subdirectory (where cargo drops per-crate PDBs).
        let sym_path = compute_sym_path();
        let sym_w = to_wide(&sym_path);
        unsafe {
            SymInitializeW(GetCurrentProcess(), sym_w.as_ptr(), 1);
            // SYMOPT_DEFERRED_LOADS(0x4) | SYMOPT_UNDNAME(0x2) | SYMOPT_LOAD_LINES(0x10)
            SymSetOptions(0x4 | 0x2 | 0x10);
        }

        let handle = thread::spawn(move || {
            let pid = unsafe { GetCurrentProcessId() };
            let self_tid = unsafe { GetCurrentThreadId() };
            let process = unsafe { GetCurrentProcess() };
            let interval = Duration::from_millis(interval_ms.max(1));
            let mut tids: Vec<u32> = Vec::new();
            let mut last_refresh: Option<Instant> = None;
            let mut rr: usize = 0;
            while !stop_t.load(Ordering::Relaxed) {
                thread::sleep(interval);
                let stale = last_refresh.map_or(true, |t| t.elapsed() >= TID_REFRESH);
                if tids.is_empty() || stale {
                    tids = list_threads(pid, self_tid);
                    last_refresh = Some(Instant::now());
                    rr = 0;
                }
                if tids.is_empty() {
                    continue;
                }
                // Round-robin: one thread per tick. This keeps the per-tick cost
                // (the perturbation) proportional to a single suspend + stack
                // walk, and gives every thread an equal expected number of
                // samples per unit time.
                let tid = tids[rr % tids.len()];
                rr = rr.wrapping_add(1);
                sample_one(process, tid, &samples_t);
            }
        });

        Sampler {
            stop,
            samples,
            handle: Some(handle),
            interval_ms,
        }
    }

    /// Stop sampling, resolve symbols, and write raw + aggregated output.
    pub fn stop(&mut self, out_dir: &str) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(h) = self.handle.take() {
            if h.join().is_err() {
                eprintln!("E3 sampler: sampling thread panicked");
            }
        }
        let samples = std::mem::take(&mut *self.samples.lock().unwrap());
        eprintln!(
            "E3 sampler: {} thread-snapshots captured (interval={}ms)",
            samples.len(),
            self.interval_ms
        );
        resolve_and_write(samples, out_dir, self.interval_ms);
        unsafe {
            SymCleanup(GetCurrentProcess());
        }
    }
}

fn compute_sym_path() -> String {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let d = dir.to_string_lossy().to_string();
            return format!("{d};{d}\\deps");
        }
    }
    String::new()
}

fn to_wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Escape a single CSV field.
fn csv_escape(s: &str) -> String {
    let needs = s.contains(',') || s.contains('"') || s.contains('\n') || s.contains('\r');
    if !needs {
        return s.to_string();
    }
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        if c == '"' {
            out.push_str("\"\"");
        } else {
            out.push(c);
        }
    }
    out.push('"');
    out
}

unsafe fn wide_to_string(p: *const u16) -> String {
    if p.is_null() {
        return String::new();
    }
    let mut v = Vec::new();
    let mut i = 0usize;
    loop {
        let c = *p.add(i);
        if c == 0 || i >= 512 {
            break;
        }
        v.push(c);
        i += 1;
    }
    String::from_utf16_lossy(&v)
}

/// Enumerate the tids of `pid`, excluding the sampler thread itself.
fn list_threads(pid: u32, self_tid: u32) -> Vec<u32> {
    let mut out = Vec::new();
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
        if snap == INVALID_HANDLE_VALUE {
            eprintln!(
                "E3 sampler: CreateToolhelp32Snapshot(threads) failed err={}",
                GetLastError()
            );
            return out;
        }
        let mut te: THREADENTRY32 = std::mem::zeroed();
        te.dwSize = size_of::<THREADENTRY32>() as u32;
        if Thread32First(snap, &mut te) != 0 {
            loop {
                if te.th32OwnerProcessID == pid && te.th32ThreadID != self_tid {
                    out.push(te.th32ThreadID);
                }
                if Thread32Next(snap, &mut te) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snap);
    }
    out
}

/// x64 `CONTEXT` must be 16-byte aligned; a plain `mem::zeroed()` on the stack is
/// only 8-byte aligned and makes `GetThreadContext` fail with err 998.
#[repr(C, align(16))]
struct AlignedContext(CONTEXT);

fn sample_one(process: HANDLE, tid: u32, sink: &Arc<Mutex<Vec<(u32, u8, Vec<u64>)>>>) {
    unsafe {
        let h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT, 0, tid);
        if h.is_null() {
            return;
        }
        if SuspendThread(h) != u32::MAX {
            let mut actx: AlignedContext = std::mem::zeroed();
            let ctx = &mut actx.0;
            // CONTEXT_FULL (x64) = CONTEXT_AMD64(0x100000) | CONTROL(0x1)
            //                     | INTEGER(0x2) | FLOATING_POINT(0x4)
            ctx.ContextFlags = 0x100007;
            if GetThreadContext(h, ctx) != 0 {
                let pcs = walk_stack(process, h, ctx);
                let stage = halo2_proofs::plonk::E3_STAGE
                    .load(std::sync::atomic::Ordering::Relaxed);
                sink.lock().unwrap().push((tid, stage, pcs));
            }
            ResumeThread(h);
        }
        CloseHandle(h);
    }
}

fn walk_stack(process: HANDLE, thread: HANDLE, ctx: &mut CONTEXT) -> Vec<u64> {
    // ADDRESS_MODE::AddrModeFlat == 3 (NOT 0; 0 is AddrMode1616 and makes
    // StackWalk64 fail immediately on x64).
    const ADDR_MODE_FLAT: i32 = 3;
    let mut sf: STACKFRAME64 = unsafe { std::mem::zeroed() };
    sf.AddrPC.Offset = ctx.Rip;
    sf.AddrPC.Mode = ADDR_MODE_FLAT;
    sf.AddrStack.Offset = ctx.Rsp;
    sf.AddrStack.Mode = ADDR_MODE_FLAT;
    sf.AddrFrame.Offset = ctx.Rbp;
    sf.AddrFrame.Mode = ADDR_MODE_FLAT;

    let mut pcs = Vec::with_capacity(MAX_FRAMES);
    sf.AddrPC.Offset = ctx.Rip;
    for _ in 0..MAX_FRAMES {
        let ok = unsafe {
            StackWalk64(
                0x8664, // IMAGE_FILE_MACHINE_AMD64
                process,
                thread,
                &mut sf,
                ctx as *mut CONTEXT as *mut _,
                None,
                Some(SymFunctionTableAccess64),
                Some(SymGetModuleBase64),
                None,
            )
        };
        if ok == 0 {
            break;
        }
        let pc = sf.AddrPC.Offset;
        if pc == 0 {
            break;
        }
        pcs.push(pc);
    }
    pcs
}

fn resolve(process: HANDLE, pc: u64) -> (String, String, String, u32) {
    unsafe {
        let base = SymGetModuleBase64(process, pc);
        let mut modinfo: IMAGEHLP_MODULEW64 = std::mem::zeroed();
        modinfo.SizeOfStruct = size_of::<IMAGEHLP_MODULEW64>() as u32;
        let module = if base != 0 && SymGetModuleInfoW64(process, base, &mut modinfo) != 0 {
            wide_to_string(modinfo.ModuleName.as_ptr())
        } else {
            format!("{base:#x}")
        };

        let buf_size = size_of::<SYMBOL_INFOW>() + SYM_NAME_LEN * 2;
        let mut buf = vec![0u8; buf_size];
        let info = buf.as_mut_ptr() as *mut SYMBOL_INFOW;
        (*info).SizeOfStruct = size_of::<SYMBOL_INFOW>() as u32;
        (*info).MaxNameLen = SYM_NAME_LEN as u32;
        let mut disp: u64 = 0;
        let func = if SymFromAddrW(process, pc, &mut disp, info) != 0 {
            // DbgHelp reports the FULL name length even when it truncated to
            // MaxNameLen, so the length must be clamped before slicing.
            let n = ((*info).NameLen as usize).min(SYM_NAME_LEN);
            let slice = std::slice::from_raw_parts((*info).Name.as_ptr(), n);
            String::from_utf16_lossy(slice)
        } else {
            format!("0x{pc:x}")
        };

        // Source attribution. Function symbols in an optimised MSVC link can
        // have stale/zero extents (code after such a symbol gets attributed to
        // it), so the file basename is a more trustworthy grouping key.
        let mut lineinfo: IMAGEHLP_LINEW64 = std::mem::zeroed();
        lineinfo.SizeOfStruct = size_of::<IMAGEHLP_LINEW64>() as u32;
        let mut ldisp: u32 = 0;
        let mut line_no: u32 = 0;
        let file = if SymGetLineFromAddrW64(process, pc, &mut ldisp, &mut lineinfo) != 0 {
            line_no = lineinfo.LineNumber;
            let full = wide_to_string(lineinfo.FileName);
            // Compress to "<crate>/src/<path>.rs" so the crate identity survives
            // (e.g. "pasta_curves/src/fields/fp.rs"). Build paths look like
            // ".../pasta_curves-0.6.0/src/fields/fp.rs".
            let parts: Vec<&str> = full
                .split(['\\', '/'])
                .filter(|p| !p.is_empty())
                .collect();
            match parts.iter().rposition(|p| *p == "src") {
                Some(i) if i > 0 => {
                    let crate_dir = parts[i - 1];
                    let crate_name = match crate_dir.rfind('-') {
                        Some(dash) if crate_dir[dash + 1..]
                            .chars()
                            .next()
                            .map_or(false, |c| c.is_ascii_digit()) =>
                        {
                            &crate_dir[..dash]
                        }
                        _ => crate_dir,
                    };
                    let mut out = String::from(crate_name);
                    for p in &parts[i..] {
                        out.push('/');
                        out.push_str(p);
                    }
                    out
                }
                _ => full,
            }
        } else {
            String::new()
        };
        (module, func, file, line_no)
    }
}

fn is_idle(func: &str) -> bool {
    IDLE_PATTERNS.iter().any(|p| func.contains(p))
}

/// Dump the module load table so recorded PCs can be re-based offline.
fn write_modules(out_dir: &str) {
    let pid = unsafe { GetCurrentProcessId() };
    let path = format!("{out_dir}/raw/e3_modules.txt");
    let Ok(f) = File::create(&path) else { return };
    let mut w = BufWriter::new(f);
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid);
        if snap == INVALID_HANDLE_VALUE {
            return;
        }
        let mut me: MODULEENTRY32W = std::mem::zeroed();
        me.dwSize = size_of::<MODULEENTRY32W>() as u32;
        let _ = writeln!(w, "# module\tbase\tsize");
        if Module32FirstW(snap, &mut me) != 0 {
            loop {
                let name = wide_to_string(me.szModule.as_ptr());
                let _ = writeln!(
                    w,
                    "{}\t{:#x}\t{:#x}",
                    name,
                    me.modBaseAddr as usize,
                    me.modBaseSize
                );
                if Module32NextW(snap, &mut me) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snap);
    }
    let _ = w.flush();
    eprintln!("E3 sampler: wrote module table -> {path}");
}
fn stage_name(code: u8) -> &'static str {
    halo2_proofs::plonk::e3_stage_name(code)
}

fn resolve_and_write(samples: Vec<(u32, u8, Vec<u64>)>, out_dir: &str, interval_ms: u64) {
    if samples.is_empty() {
        eprintln!("E3 sampler: no samples collected");
        return;
    }

    // ---- per-thread accounting (wall ticks vs busy ticks) ------------------
    let mut thread_ticks: HashMap<u32, u64> = HashMap::new();
    for (tid, _, _) in &samples {
        *thread_ticks.entry(*tid).or_insert(0) += 1;
    }

    // ---- resolve every distinct pc once -----------------------------------
    let process = unsafe { GetCurrentProcess() };
    let mut name_of: HashMap<u64, (String, String, String, u32)> = HashMap::new();
    for (_, _, pcs) in &samples {
        for pc in pcs {
            if !name_of.contains_key(pc) {
                name_of.insert(*pc, resolve(process, *pc));
            }
        }
    }

    // ---- aggregate --------------------------------------------------------
    #[derive(Default)]
    struct Agg {
        exclusive: u64,
        exclusive_busy: u64,
        inclusive: u64,
        inclusive_busy: u64,
    }
    let mut by_func: HashMap<(String, String), Agg> = HashMap::new();
    let mut by_stage_func: HashMap<(u8, String, String), u64> = HashMap::new();
    let mut by_file: HashMap<String, u64> = HashMap::new();
    // Coverage = number of (busy) samples whose stack contains the function at
    // least once. Unlike the inclusive count this is not inflated by recursion,
    // so it is the right measure of "how much CPU does this routine's subtree
    // cover" (e.g. FFT butterflies, MSM bucket accumulation).
    let mut coverage: HashMap<(String, String), u64> = HashMap::new();
    let mut by_stage_file: HashMap<(u8, String), u64> = HashMap::new();
    let mut by_line: HashMap<(String, u32, String), u64> = HashMap::new();
    let mut by_caller: HashMap<(String, String, u8), u64> = HashMap::new();
    let mut thread_idle: HashMap<u32, u64> = HashMap::new();
    let mut stage_ticks: HashMap<u8, (u64, u64)> = HashMap::new(); // (live, busy)
    let mut total_busy = 0u64;

    for (tid, stage, pcs) in &samples {
        let leaf_func = pcs
            .first()
            .and_then(|pc| name_of.get(pc))
            .map(|(_, f, _, _)| f.clone())
            .unwrap_or_default();
        let idle = is_idle(&leaf_func);
        // Caller attribution: parent frame of the leaf.
        if !idle && pcs.len() >= 2 {
            let caller = name_of
                .get(&pcs[1])
                .map(|(_, f, _, _)| f.clone())
                .unwrap_or_default();
            *by_caller.entry((leaf_func.clone(), caller, *stage)).or_insert(0) += 1;
        }
        let st = stage_ticks.entry(*stage).or_insert((0, 0));
        st.0 += 1;
        if idle {
            *thread_idle.entry(*tid).or_insert(0) += 1;
        } else {
            total_busy += 1;
            st.1 += 1;
        }
        if !idle {
            let mut seen: std::collections::HashSet<(String, String)> =
                std::collections::HashSet::new();
            for pc in pcs.iter() {
                if let Some((m, f, _, _)) = name_of.get(pc) {
                    seen.insert((m.clone(), f.clone()));
                }
            }
            for key in seen {
                *coverage.entry(key).or_insert(0) += 1;
            }
        }
        for (i, pc) in pcs.iter().enumerate() {
            let (module, func, file, line_no) = match name_of.get(pc) {
                Some((m, f, src, ln)) => (m.clone(), f.clone(), src.clone(), *ln),
                None => (String::new(), format!("0x{pc:x}"), String::new(), 0),
            };
            let file_key = if file.is_empty() {
                "<unknown>".to_string()
            } else {
                file.clone()
            };
            let e = by_func.entry((module, func.clone())).or_default();
            e.inclusive += 1;
            if !idle {
                e.inclusive_busy += 1;
            }
            if i == 0 {
                e.exclusive += 1;
                if !idle {
                    e.exclusive_busy += 1;
                    *by_stage_func
                        .entry((*stage, func.clone(), file_key.clone()))
                        .or_insert(0) += 1;
                    *by_file.entry(file_key.clone()).or_insert(0) += 1;
                    *by_stage_file.entry((*stage, file_key.clone())).or_insert(0) += 1;
                    *by_line
                        .entry((file_key.clone(), line_no, func.clone()))
                        .or_insert(0) += 1;
                }
            }
        }
    }

    let total_samples = samples.len() as f64;
    let total_busy_f = total_busy as f64;

    // ---- raw samples + module table ---------------------------------------
    let raw_path = format!("{out_dir}/raw/e3_samples.txt");
    if let Ok(f) = File::create(&raw_path) {
        let mut w = BufWriter::new(f);
        let _ = writeln!(w, "# tid\tstage\tpc_hex...");
        for (tid, stage, pcs) in &samples {
            let _ = writeln!(
                w,
                "{tid}\t{stage}\t{}",
                pcs.iter()
                    .map(|p| format!("{p:x}"))
                    .collect::<Vec<_>>()
                    .join(" ")
            );
        }
        let _ = w.flush();
        eprintln!("E3 sampler: wrote raw samples -> {raw_path}");
    }
    write_modules(out_dir);

    // ---- hot functions (sorted by busy-exclusive = CPU self time) ---------
    let mut rows: Vec<(String, String, u64, u64, u64, u64, u64)> = by_func
        .into_iter()
        .map(|((m, f), a)| {
            let cov = coverage.get(&(m.clone(), f.clone())).copied().unwrap_or(0);
            (
                f,
                m,
                a.exclusive,
                a.exclusive_busy,
                a.inclusive,
                a.inclusive_busy,
                cov,
            )
        })
        .collect();
    rows.sort_by(|a, b| b.3.cmp(&a.3).then(b.2.cmp(&a.2)));

    let csv_path = format!("{out_dir}/profile/hot_functions.csv");
    if let Ok(f) = File::create(&csv_path) {
        let mut w = BufWriter::new(f);
        let _ = writeln!(
            w,
            "function,module,exclusive_samples,busy_exclusive_samples,busy_self_pct,inclusive_samples,busy_inclusive_samples,busy_incl_pct,coverage_samples,coverage_pct,idle_leaf"
        );
        for (func, module, ex, ex_busy, incl, incl_busy, cov) in &rows {
            let cov_pct = if total_busy_f > 0.0 {
                *cov as f64 / total_busy_f * 100.0
            } else {
                0.0
            };
            let self_pct = if total_busy_f > 0.0 {
                *ex_busy as f64 / total_busy_f * 100.0
            } else {
                0.0
            };
            let incl_pct = if total_busy_f > 0.0 {
                *incl_busy as f64 / total_busy_f * 100.0
            } else {
                0.0
            };
            let _ = writeln!(
                w,
                "{},{},{},{},{:.4},{},{},{:.4},{},{:.4},{}",
                csv_escape(func),
                csv_escape(module),
                ex,
                ex_busy,
                self_pct,
                incl,
                incl_busy,
                incl_pct,
                cov,
                cov_pct,
                is_idle(func) as u8
            );
        }
        let _ = w.flush();
        eprintln!("E3 sampler: wrote {} functions -> {csv_path}", rows.len());
    }

    // ---- per-thread utilisation -------------------------------------------
    let thr_path = format!("{out_dir}/profile/threads.csv");
    if let Ok(f) = File::create(&thr_path) {
        let mut w = BufWriter::new(f);
        let _ = writeln!(w, "tid,ticks,idle_ticks,busy_ticks,busy_pct");
        let mut tids: Vec<&u32> = thread_ticks.keys().collect();
        tids.sort();
        for tid in tids {
            let ticks = thread_ticks[tid];
            let idle = *thread_idle.get(tid).unwrap_or(&0);
            let busy = ticks - idle;
            let pct = if ticks > 0 {
                busy as f64 / ticks as f64 * 100.0
            } else {
                0.0
            };
            let _ = writeln!(w, "{tid},{ticks},{idle},{busy},{pct:.2}");
        }
        let _ = w.flush();
        eprintln!("E3 sampler: wrote per-thread utilisation -> {thr_path}");
    }

    // ---- stage totals (cross-check against E2 wall-clock shares) ----------
    let stage_path = format!("{out_dir}/profile/stage_samples.csv");
    if let Ok(f) = File::create(&stage_path) {
        let mut w = BufWriter::new(f);
        let _ = writeln!(
            w,
            "stage_code,stage,live_samples,busy_samples,busy_pct_of_all_busy,live_pct"
        );
        let mut keys: Vec<u8> = stage_ticks.keys().copied().collect();
        keys.sort();
        for code in keys {
            let (live, busy) = stage_ticks[&code];
            let bp = if total_busy_f > 0.0 {
                busy as f64 / total_busy_f * 100.0
            } else {
                0.0
            };
            let lp = live as f64 / total_samples.max(1.0) * 100.0;
            let _ = writeln!(
                w,
                "{},{},{},{},{:.3},{:.3}",
                code,
                stage_name(code),
                live,
                busy,
                bp,
                lp
            );
        }
        let _ = w.flush();
        eprintln!("E3 sampler: wrote per-stage totals -> {stage_path}");
    }

    // ---- per-stage hot functions (the E3.4 evidence table) ---------------
    let sf_path = format!("{out_dir}/profile/stage_functions.csv");
    if let Ok(f) = File::create(&sf_path) {
        let mut w = BufWriter::new(f);
        let _ = writeln!(
            w,
            "stage_code,stage,function,source_file,busy_self_samples,pct_within_stage,pct_of_all_busy"
        );
        let mut items: Vec<((u8, String, String), u64)> = by_stage_func.into_iter().collect();
        items.sort_by(|a, b| a.0 .0.cmp(&b.0 .0).then(b.1.cmp(&a.1)));
        for ((code, func, file), n) in items {
            let stage_busy = stage_ticks.get(&code).map(|v| v.1).unwrap_or(0).max(1);
            let _ = writeln!(
                w,
                "{},{},{},{},{},{:.3},{:.3}",
                code,
                stage_name(code),
                csv_escape(&func),
                csv_escape(&file),
                n,
                n as f64 / stage_busy as f64 * 100.0,
                n as f64 / total_busy_f.max(1.0) * 100.0
            );
        }
        let _ = w.flush();
        eprintln!("E3 sampler: wrote per-stage functions -> {sf_path}");
    }

    // ---- per-source-file totals (robust to symbol-extent artifacts) ------
    let files_path = format!("{out_dir}/profile/source_files.csv");
    if let Ok(f) = File::create(&files_path) {
        let mut w = BufWriter::new(f);
        let _ = writeln!(w, "source_file,busy_self_samples,pct_of_all_busy");
        let mut items: Vec<(String, u64)> = by_file.into_iter().collect();
        items.sort_by(|a, b| b.1.cmp(&a.1));
        for (file, n) in items {
            let _ = writeln!(
                w,
                "{},{},{:.3}",
                csv_escape(&file),
                n,
                n as f64 / total_busy_f.max(1.0) * 100.0
            );
        }
        let _ = w.flush();
        eprintln!("E3 sampler: wrote per-source-file totals -> {files_path}");
    }

    // ---- per-stage source files ------------------------------------------
    let sf2_path = format!("{out_dir}/profile/stage_source_files.csv");
    if let Ok(f) = File::create(&sf2_path) {
        let mut w = BufWriter::new(f);
        let _ = writeln!(
            w,
            "stage_code,stage,source_file,busy_self_samples,pct_within_stage"
        );
        let mut items: Vec<((u8, String), u64)> = by_stage_file.into_iter().collect();
        items.sort_by(|a, b| a.0 .0.cmp(&b.0 .0).then(b.1.cmp(&a.1)));
        for ((code, file), n) in items {
            let stage_busy = stage_ticks.get(&code).map(|v| v.1).unwrap_or(0).max(1);
            let _ = writeln!(
                w,
                "{},{},{},{},{:.3}",
                code,
                stage_name(code),
                csv_escape(&file),
                n,
                n as f64 / stage_busy as f64 * 100.0
            );
        }
        let _ = w.flush();
        eprintln!("E3 sampler: wrote per-stage source files -> {sf2_path}");
    }

    // ---- caller attribution (who invokes each hot function) --------------
    let callers_path = format!("{out_dir}/profile/hot_callers.csv");
    if let Ok(f) = File::create(&callers_path) {
        let mut w = BufWriter::new(f);
        let _ = writeln!(w, "callee,caller,stage,busy_samples,pct_of_callee");
        let mut callee_total: HashMap<String, u64> = HashMap::new();
        for ((callee, _, _), n) in by_caller.iter() {
            *callee_total.entry(callee.clone()).or_insert(0) += n;
        }
        let mut items: Vec<((String, String, u8), u64)> = by_caller.into_iter().collect();
        items.sort_by(|a, b| b.1.cmp(&a.1));
        for ((callee, caller, stage), n) in items.into_iter().take(150) {
            let total = callee_total.get(&callee).copied().unwrap_or(n).max(1);
            let _ = writeln!(
                w,
                "{},{},{},{},{:.3}",
                csv_escape(&callee),
                csv_escape(&caller),
                stage,
                n,
                n as f64 / total as f64 * 100.0
            );
        }
        let _ = w.flush();
        eprintln!("E3 sampler: wrote caller attribution -> {callers_path}");
    }

    // ---- hottest source lines --------------------------------------------
    let lines_path = format!("{out_dir}/profile/hot_lines.csv");
    if let Ok(f) = File::create(&lines_path) {
        let mut w = BufWriter::new(f);
        let _ = writeln!(
            w,
            "source_file,line,function,busy_self_samples,pct_of_all_busy"
        );
        let mut items: Vec<((String, u32, String), u64)> = by_line.into_iter().collect();
        items.sort_by(|a, b| b.1.cmp(&a.1));
        for ((file, line, func), n) in items.into_iter().take(200) {
            let _ = writeln!(
                w,
                "{},{},{},{},{:.3}",
                csv_escape(&file),
                line,
                csv_escape(&func),
                n,
                n as f64 / total_busy_f.max(1.0) * 100.0
            );
        }
        let _ = w.flush();
        eprintln!("E3 sampler: wrote hottest source lines -> {lines_path}");
    }

    // ---- summary ----------------------------------------------------------
    // With round-robin sampling each thread's busy/ticks ratio is an unbiased
    // estimate of the fraction of its lifetime spent executing; summing those
    // ratios gives the mean number of concurrently-busy threads (= achieved CPU
    // parallelism) during the window.
    let mean_busy: f64 = thread_ticks
        .iter()
        .map(|(tid, ticks)| {
            let idle = *thread_idle.get(tid).unwrap_or(&0);
            (*ticks - idle) as f64 / (*ticks).max(1) as f64
        })
        .sum();
    eprintln!(
        "E3 sampler: summary ticks={} busy={} ({:.1}%) threads={} mean_busy_threads={:.2}",
        samples.len(),
        total_busy,
        total_busy as f64 / total_samples.max(1.0) * 100.0,
        thread_ticks.len(),
        mean_busy
    );
}
