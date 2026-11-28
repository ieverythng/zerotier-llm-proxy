# Watson Local inference

Watson Local serves registered model profiles using one workstation GPU. It is distinct from the legacy Watson/Hermes stack and from the Universal Agentic Harness's agent identity model.

## Language

**Registered model**: A named model artifact with an approved engine and serving alias; registration does not mean it is loaded or proven at every context.
_Avoid_: Running model, agent ID

**Model profile**: A launchable combination of a registered model, context allocation, and runtime settings. Two profiles of the same model can have different memory and performance evidence.

**Context allocation**: Total tokens reserved for the model's input and output. It is distinct from a trial's actual evaluated prompt tokens and from the model's published architecture limit.
_Avoid_: Maximum prompt size

**Resident model**: The one verified profile currently loaded on the GPU. A model shown in a picker need not be resident.

**Stack stop**: Shutdown of the verified owned workload, including the inference route, with the terminal still open. A busy stop is refused, not scheduled.
_Avoid_: Unload, quit

**Model unload**: Releasing the resident model but keeping the inference route and terminal available.

**Terminal detach**: Closing the launcher view without changing the workload's lifetime.
_Avoid_: Stack stop

**Benchmark run**: A bounded experiment whose admission spans every planned request and the gaps between them. A temporarily idle request slot does not mean a run has ended.

**Memory evidence**: Observed free system RAM and GPU memory for a particular tested profile and workload. A configured or interpolated estimate is not a measurement or a fit guarantee.
