//! Linux device adapter. The Pulse server also covers PipeWire's Pulse interface.
use crate::{Device, Error};
use libpulse_binding::{
    callbacks::ListResult,
    context::{Context, FlagSet, State},
    def::BufferAttr,
    mainloop::standard::{IterateResult, Mainloop},
    operation::State as OperationState,
    sample::{Format, Spec},
    stream::Direction,
};
use libpulse_simple_binding::Simple;
use std::{
    cell::RefCell,
    rc::Rc,
    sync::mpsc::SyncSender,
    thread,
    time::{Duration, Instant},
};

fn iterate(mainloop: &mut Mainloop, deadline: Instant) -> Result<(), Error> {
    if Instant::now() >= deadline {
        return Err(Error::Device);
    }
    if !matches!(mainloop.iterate(false), IterateResult::Success(_)) {
        return Err(Error::Device);
    }
    thread::sleep(Duration::from_millis(2));
    Ok(())
}

pub fn devices() -> Result<Vec<Device>, Error> {
    let mut mainloop = Mainloop::new().ok_or(Error::Device)?;
    let mut context = Context::new(&mainloop, "meeting-supporter devices").ok_or(Error::Device)?;
    context
        .connect(None, FlagSet::NOAUTOSPAWN, None)
        .map_err(|_| Error::Device)?;
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        match context.get_state() {
            State::Ready => break,
            State::Failed | State::Terminated => return Err(Error::Device),
            _ => iterate(&mut mainloop, deadline)?,
        }
    }
    let defaults = Rc::new(RefCell::new((None, None)));
    let target = Rc::clone(&defaults);
    let operation = context.introspect().get_server_info(move |info| {
        *target.borrow_mut() = (
            info.default_source_name.as_ref().map(ToString::to_string),
            info.default_sink_name.as_ref().map(ToString::to_string),
        );
    });
    while operation.get_state() == OperationState::Running {
        iterate(&mut mainloop, deadline)?;
    }
    let result = Rc::new(RefCell::new(Vec::new()));
    let target = Rc::clone(&result);
    let failed = Rc::new(RefCell::new(false));
    let error = Rc::clone(&failed);
    let operation = context
        .introspect()
        .get_source_info_list(move |item| match item {
            ListResult::Item(info) => {
                if let Some(name) = &info.name {
                    let defaults = defaults.borrow();
                    let is_monitor = info.monitor_of_sink.is_some();
                    let is_default = if is_monitor {
                        info.monitor_of_sink_name.as_deref() == defaults.1.as_deref()
                    } else {
                        Some(name.as_ref()) == defaults.0.as_deref()
                    };
                    target.borrow_mut().push(Device {
                        index: name.to_string(),
                        name: info.description.as_deref().unwrap_or(name).to_owned(),
                        is_monitor,
                        is_default,
                        hostapi: "PulseAudio",
                        capture: "rust",
                    });
                }
            }
            ListResult::Error => *error.borrow_mut() = true,
            ListResult::End => {}
        });
    while operation.get_state() == OperationState::Running {
        iterate(&mut mainloop, deadline)?;
    }
    context.disconnect();
    if *failed.borrow() {
        return Err(Error::Device);
    }
    let devices = std::mem::take(&mut *result.borrow_mut());
    Ok(devices)
}

pub enum CaptureEvent {
    Started,
    Frame(Box<[u8; 960]>),
    Failed,
}

pub fn capture(
    device: String,
    sender: SyncSender<CaptureEvent>,
    failed: std::sync::Arc<std::sync::atomic::AtomicBool>,
) {
    let spec = Spec {
        format: Format::S16le,
        channels: 1,
        rate: 16_000,
    };
    let buffer = BufferAttr {
        maxlength: 32_000,
        tlength: u32::MAX,
        prebuf: u32::MAX,
        minreq: u32::MAX,
        fragsize: 960,
    };
    let result = Simple::new(
        None,
        "meeting-supporter",
        Direction::Record,
        Some(&device),
        "meeting audio",
        &spec,
        None,
        Some(&buffer),
    );
    let Ok(source) = result else {
        failed.store(true, std::sync::atomic::Ordering::Release);
        let _ = sender.send(CaptureEvent::Failed);
        return;
    };
    if sender.send(CaptureEvent::Started).is_err() {
        return;
    }
    loop {
        let mut pcm = Box::new([0; 960]);
        if source.read(pcm.as_mut()).is_err() {
            failed.store(true, std::sync::atomic::Ordering::Release);
            let _ = sender.send(CaptureEvent::Failed);
            return;
        }
        // An overflow ends capture explicitly. No hidden holes in a successful WAV.
        if sender.try_send(CaptureEvent::Frame(pcm)).is_err() {
            failed.store(true, std::sync::atomic::Ordering::Release);
            let _ = sender.send(CaptureEvent::Failed);
            return;
        }
    }
}
