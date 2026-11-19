# Interpreting a GA100 card's configuration

The CMP 170HX product name covers multiple configurations. A die can have hardware excluded after production testing, and a product configuration can disable additional hardware. Driver-visible HBM, SM count and link training report what the current system can use. Those measurements alone cannot identify the cause of every disabled partition or establish a safe target configuration for a card.

Kate reports both nominal 8 GB and 10 GB products. Among ten nominal 8 GB specimens, one exposed 64 GB of usable memory after her experiments and nine exposed 80 GB. The observations apply to that sample. Her discussion also explains how address aliasing can make apparent capacity exceed usable memory. [Author's results](https://blog.kate.land/posts/using-voltage-glitching-to-unlock-the-a100/)

## Published software memory route

The independent [cmpunlocker](https://github.com/amoghmunikote/cmpunlocker) installer patches NVIDIA driver modules and selects framebuffer geometry from the PCI device ID. On `10de:20c2` it targets 65536 MiB (64 GiB); on `10de:2082` it targets 40960 MiB (40 GiB). It does not burn fuse records or select an 80 GiB profile. The nominal `--profile` option changes installer metadata; the PCI ID still determines geometry. A target capacity is not a readback test.

From gpuglitch on a dedicated GPU host, take a baseline with `ga100ctl gpu inspect` and `ga100ctl memory-check`, then run:

```sh
git clone https://github.com/amoghmunikote/cmpunlocker.git ../cmpunlocker
git -C ../cmpunlocker checkout 2c7e2ecd7944e791fec040d2680036beb4a9327b
.venv/bin/ga100ctl memory-enable --source ../cmpunlocker --confirm
```

The wrapper requires exactly one visible NVIDIA GPU with a supported PCI ID and a clean checkout at that reviewed commit. It records an event, then runs the upstream installer through `sudo` with IOMMU, Gen2 boot service and passthrough setup disabled. The installer still removes conflicting NVIDIA DKMS modules, installs patched kernel modules and writes `/etc/modprobe.d/cmp-pcie-gen2.conf`; it can download matching driver sources on first install. A successful wrapper result is `pending_power_cycle`. Shut down and remove GPU power, boot again, then rerun both inspection and memory readback before claiming capacity. Consult the [upstream installer](https://github.com/amoghmunikote/cmpunlocker/blob/2c7e2ecd7944e791fec040d2680036beb4a9327b/install.sh) for its host requirements and removal path.

## Evidence for a before-and-after comparison

1. Record the physical card label and any known prior modification. Capture a first inspection, including the PCI address and subsystem identifiers, available GPU UUID or serial, reported HBM, driver version, VBIOS version and current link state. Label the card stock only if its history supports that claim.
2. Record a dated observation of any independently performed change, including who observed it, its provenance and the exact card identity. Keep unknowns explicit. Externally supplied fuse observations are records about that specimen. A mask copied from a different specimen carries no information about this one's defective components.
3. Collect a fresh inspection and compare entries for the same physical card. Driver or VBIOS changes, PCIe power management, ECC settings and changed cooling can affect comparisons. Identify any operator-reported details separately from measurements.
4. Exercise a selected allocation and record the number of bytes tested, the pattern, and any errors. Keep the capacity claim within the tested coverage. Check SM count, negotiated PCIe state and peer availability under the relevant workload. Retain failed initializations, unsupported queries, and thermal or compute errors in the history.

This toolkit covers inventory, measurement and the bounded cmpunlocker software installation above. Raw fuse reads, partition-map validation, voltage fault injection and permanent programming are outside its command set. A successful workload validates only the configuration, memory range and conditions exercised during that run.
