import cv2
import os
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import *
from time import sleep, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
from path_utils import replace_split_token

# Try to import GPU acceleration libraries
try:
    import torch
    import torch.nn.functional as F
    import torchvision.transforms.functional as TF
    if torch.cuda.is_available():
        TORCH_AVAILABLE = True
        DEVICE = torch.device('cuda')
        print(f"PyTorch GPU acceleration available: {torch.cuda.get_device_name()}")
    else:
        TORCH_AVAILABLE = False
        DEVICE = torch.device('cpu')
        print("PyTorch installed but CUDA not available")
except ImportError:
    TORCH_AVAILABLE = False
    DEVICE = None
    print("PyTorch not available. Install with: pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121")

# Try to import GPU monitoring utilities
try:
    import GPUtil
    GPU_MONITORING_AVAILABLE = True
except ImportError:
    GPU_MONITORING_AVAILABLE = False
    print("GPUtil not available. Install with: pip install GPUtil")

class PreviewHandler:   
    def __init__(self, mainwindow, workerSignals):
        self.mainwindow = mainwindow
        self.workerSignals = workerSignals
        self.max_previews = 300
        
        # Check for GPU acceleration
        self.use_gpu = TORCH_AVAILABLE and torch.cuda.is_available()
        self.device = DEVICE if self.use_gpu else None
        self.max_workers = min(8, os.cpu_count())  # Limit concurrent threads
        
        if self.use_gpu:
            # Clear GPU cache at start
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            
            # Set memory fraction to avoid OOM
            torch.cuda.set_per_process_memory_fraction(0.8)
            
            print(f"GPU acceleration enabled - {torch.cuda.get_device_name()}")
            print(f"GPU Memory: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
        else:
            print("GPU acceleration not available, using multi-threading")

    def _preview_log(self, reel_id, split, message):
        print(f"[preview][reel={reel_id}][split={split}] {message}")
    
    def get_gpu_info(self):
        """Get GPU information and usage"""
        if not self.use_gpu:
            return "GPU not in use"
            
        try:
            # PyTorch GPU info
            allocated = torch.cuda.memory_allocated() / 1024**2
            reserved = torch.cuda.memory_reserved() / 1024**2
            
            info = f"GPU Memory: {allocated:.1f}MB allocated / {reserved:.1f}MB reserved"
            
            # Add GPUtil info if available
            if GPU_MONITORING_AVAILABLE:
                gpus = GPUtil.getGPUs()
                if gpus:
                    gpu = gpus[0]
                    info += f" | Load: {gpu.load*100:.1f}% | Temp: {gpu.temperature}°C"
            
            return info
        except Exception as e:
            return f"Error getting GPU info: {e}"
    
    def log_gpu_usage(self, operation):
        """Log current GPU usage for monitoring"""
        if self.use_gpu:
            print(f"[{operation}] {self.get_gpu_info()}")
    
    def process_frame_gpu(self, frame):
        """Process a single frame using GPU with PyTorch"""
        if not self.use_gpu:
            return self.convert_to_qt(frame)
            
        try:
            # Convert frame to tensor and move to GPU
            # Frame is in BGR format from OpenCV, shape: (H, W, 3)
            frame_tensor = torch.from_numpy(frame).to(self.device, dtype=torch.float32)
            
            # Rearrange dimensions from HWC to CHW for PyTorch
            frame_tensor = frame_tensor.permute(2, 0, 1)
            
            # Convert BGR to RGB (swap channels 0 and 2)
            frame_tensor = frame_tensor[[2, 1, 0], :, :]
            
            # Add batch dimension for resize operation
            frame_tensor = frame_tensor.unsqueeze(0)
            
            # Resize using PyTorch (bilinear interpolation)
            resized_tensor = F.interpolate(
                frame_tensor,
                size=(202, 270),
                mode='bilinear',
                align_corners=False
            )
            
            # Remove batch dimension and convert back to HWC
            resized_tensor = resized_tensor.squeeze(0).permute(1, 2, 0)
            
            # Convert back to numpy and ensure uint8
            resized_numpy = resized_tensor.clamp(0, 255).byte().cpu().numpy()
            
            # Convert to Qt format
            return self.numpy_to_qt(resized_numpy)
            
        except Exception as e:
            print(f"GPU processing failed: {e}")
            # Clear GPU cache on error
            if self.use_gpu:
                torch.cuda.empty_cache()
            # Fallback to CPU processing
            return self.convert_to_qt(frame)
    
    def process_frame_batch_gpu_torch(self, frames):
        """Process multiple frames at once using PyTorch batching"""
        if not self.use_gpu or len(frames) == 0:
            return None
            
        try:
            # More efficient: create tensor directly on GPU
            # First, create numpy array batch
            frame_array = np.stack(frames)  # Shape: [batch, H, W, 3]
            
            # Move to GPU and convert in one operation
            with torch.no_grad():  # Disable gradient computation for inference
                # Convert to tensor and move to GPU
                batch_tensor = torch.from_numpy(frame_array).to(self.device, dtype=torch.float32)
                
                # Rearrange from BHWC to BCHW for PyTorch
                batch_tensor = batch_tensor.permute(0, 3, 1, 2)
                
                # Convert BGR to RGB (swap channels 0 and 2) for entire batch
                batch_tensor = batch_tensor[:, [2, 1, 0], :, :]
                
                # Batch resize - all frames at once
                resized_batch = F.interpolate(
                    batch_tensor,
                    size=(202, 270),
                    mode='bilinear',
                    align_corners=False
                )
                
                # Convert back to BHWC and to uint8
                resized_batch = resized_batch.permute(0, 2, 3, 1)
                resized_numpy = resized_batch.clamp(0, 255).byte().cpu().numpy()
            
            # Log actual memory usage
            if self.use_gpu:
                allocated = torch.cuda.memory_allocated() / 1024**2
                print(f"  GPU Memory during batch processing: {allocated:.1f}MB")
            
            return resized_numpy
            
        except Exception as e:
            print(f"Batch GPU processing failed: {e}")
            if self.use_gpu:
                torch.cuda.empty_cache()
            return None
    
    def process_frame_batch_optimized(self, frames, frame_indices, frame_type):
        """Process frames using GPU if available, otherwise multi-threaded CPU"""
        if not frames:
            return {}
        
        processed_frames = {}
        
        if self.use_gpu:
            # Log GPU usage before processing
            self.log_gpu_usage(f"Before {frame_type} processing")
            
            # Process ALL frames in one batch for maximum GPU efficiency
            batch_size = min(len(frames), 32)  # Process up to 32 frames at once
            
            # Pre-allocate result dictionary
            for batch_start in range(0, len(frames), batch_size):
                batch_end = min(batch_start + batch_size, len(frames))
                batch_frames = frames[batch_start:batch_end]
                batch_indices = frame_indices[batch_start:batch_end]
                
                with torch.cuda.amp.autocast():  # Use mixed precision for speed
                    # Try batch processing
                    batch_result = self.process_frame_batch_gpu_torch(batch_frames)
                
                if batch_result is not None:
                    # Convert each processed frame to Qt format
                    for i, (processed_frame, idx) in enumerate(zip(batch_result, batch_indices)):
                        qt_frame = self.numpy_to_qt(processed_frame)
                        if frame_type == "start_frame":
                            key = f"start_frame{idx}"
                        else:
                            key = f"end_frame{idx}"
                        processed_frames[key] = qt_frame
                else:
                    # Fallback to individual frame processing
                    for frame, idx in zip(batch_frames, batch_indices):
                        qt_frame = self.process_frame_gpu(frame)
                        if frame_type == "start_frame":
                            key = f"start_frame{idx}"
                        else:
                            key = f"end_frame{idx}"
                        processed_frames[key] = qt_frame
            
            # Clear GPU cache after all processing
            if self.use_gpu:
                torch.cuda.synchronize()  # Ensure all GPU operations complete
                self.log_gpu_usage(f"After {frame_type} processing")
            
        else:
            # Multi-threaded CPU processing
            def process_frame_with_index(data):
                frame, idx = data
                qt_frame = self.convert_to_qt(frame)
                if frame_type == "start_frame":
                    key = f"start_frame{idx}"
                else:
                    key = f"end_frame{idx}"
                return key, qt_frame
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                frame_data = list(zip(frames, frame_indices))
                futures = {executor.submit(process_frame_with_index, data): data for data in frame_data}
                
                for future in as_completed(futures):
                    try:
                        key, qt_frame = future.result()
                        processed_frames[key] = qt_frame
                    except Exception as e:
                        print(f"CPU frame processing failed: {e}")
        
        return processed_frames
    
    def numpy_to_qt(self, rgb_array):
        """Convert numpy RGB array to QPixmap"""
        try:
            # Ensure the array is contiguous and uint8
            if rgb_array.dtype != np.uint8:
                rgb_array = rgb_array.astype(np.uint8)
            
            # Make sure array is contiguous
            if not rgb_array.flags['C_CONTIGUOUS']:
                rgb_array = np.ascontiguousarray(rgb_array)
            
            h, w, ch = rgb_array.shape
            bytes_per_line = ch * w
            convert_to_Qt_format = QImage(rgb_array.data, w, h, bytes_per_line, QImage.Format_RGB888)
            frame = QPixmap.fromImage(convert_to_Qt_format)
            # Note: Image is already resized, no need to scale again
            return frame
        except Exception as e:
            print(f"Qt conversion failed: {e}")
            frame = QPixmap(".\\framefail.jpg")
            return frame

    def preview_manager(self):
        """ filter list to only include items that have state queued""" 
        while not getattr(self.mainwindow, "stop_background_workers", False):
            with self.mainwindow.queue_lock:
                queued_reels = [
                    d
                    for d in self.mainwindow.queue_batches
                    if d['state'] == "RECORDED"
                    and d['preview_loaded'] is False
                    and d.get("prep_state", "READY") == "READY"
                ]
                previews_loaded = [d for d in self.mainwindow.queue_batches if d['preview_loaded'] is True]
            self.mainwindow.previews_loaded = len(previews_loaded)
            self.mainwindow.signal_previews_loaded.emit(self.mainwindow.previews_loaded)
            if len(queued_reels) == 0:
                sleep(1)
                continue
            next_reel = queued_reels[-1]
            if 'preview_data' in next_reel:
                continue
            id = next_reel['id']
            self.mainwindow.current_cache_id = id
            next_reel['state'] = "CACHING"
            self.fetch_previews(id, False)
            next_reel['state'] = "CACHED"

    def fetch_previews(self, id, set_preview):
        with self.mainwindow.queue_lock:
            reel = [d for d in self.mainwindow.queue_batches if d['id'] == id][0]
        self.reel_data = reel
        reel_id = self.reel_data['id']
        splits = reel['splits']
        if splits == 0:
            self.get_previews(reel_id, 0, set_preview)
        else:
            for split in range(splits + 1):
                self.get_previews(reel_id, split, set_preview)

    def set_previews(self, id, split):
        with self.mainwindow.queue_lock:
            reel = [d for d in self.mainwindow.queue_batches if d['id'] == id][0]
        if "preview_data" not in reel or split not in reel["preview_data"]:
            self._preview_log(id, split, "preview_data missing for split; attempting regeneration")
            self.get_previews(id, split, False)
        if "preview_data" not in reel or split not in reel["preview_data"]:
            self._preview_log(id, split, "preview_data still missing after regeneration")
            return
        reel_previews = reel['preview_data'][split]
        start_count = len(reel_previews.get("start_previews", {}))
        end_count = len(reel_previews.get("end_previews", {}))
        if start_count == 0 or end_count == 0:
            self._preview_log(id, split, f"empty preview dicts start={start_count} end={end_count}; regenerating")
            self.get_previews(id, split, False)
            reel_previews = reel['preview_data'].get(split, reel_previews)
        self.mainwindow.previews_loading = True
        try:
            split_state = self._get_or_create_split_state(reel, split, reel_previews)
        except ValueError as exc:
            self._preview_log(id, split, f"failed to build split state: {exc}")
            self.mainwindow.previews_loading = False
            return
        self._sync_mainwindow_positions(split_state)

        start_gui_frame_data = self._render_strip(
            reel_previews['start_previews'],
            "start_frame",
            "sf",
            self.mainwindow.start_interval,
            split_state['start_position'],
        )
        reel_previews['start_gui_frame_data'] = start_gui_frame_data

        end_gui_frame_data = self._render_strip(
            reel_previews['end_previews'],
            "end_frame",
            "ef",
            self.mainwindow.end_interval,
            split_state['end_position'],
        )
        reel_previews['end_gui_frame_data'] = end_gui_frame_data
        self._apply_auto_split_match_suggestion(reel, split, reel_previews)
        self.mainwindow.previews_loading = False

    def get_previews(self, reel_id, split, set_preview):
        with self.mainwindow.queue_lock:
            reel_data = [d for d in self.mainwindow.queue_batches if d['id'] == reel_id][0]
        splits = reel_data['splits']
        video_dir = reel_data['video_dir']
        print(video_dir)
        if split > 0:
            split_no = split + 1
            video_dir = replace_split_token(video_dir, split_no)
        print("getting previews for:", video_dir)
        video = cv2.VideoCapture(video_dir)
        fps = video.get(cv2.CAP_PROP_FPS)               #init video
        self.total_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
        self._preview_log(reel_id, split, f"video={video_dir} opened={video.isOpened()} fps={fps} total_frames={self.total_frames}")

        if not video.isOpened() or fps <= 0 or self.total_frames <= 0:
            self._preview_log(reel_id, split, "invalid video stream for previews; creating fallback preview frames")
            fallback = QPixmap(".\\framefail.jpg")
            start_previews = {"start_frame0": fallback}
            end_previews = {"end_frame0": fallback}
            reel_previews = {"start_previews": start_previews, "end_previews": end_previews}
            if 'preview_data' not in reel_data:
                reel_data['preview_data'] = {}
            if split == reel_data['splits']:
                reel_data['preview_loaded'] = True
            reel_data['preview_data'][split] = reel_previews
            reel_data[split] = {}
            reel_data[split]['edited'] = False
            video.release()
            return

        
        if splits > 0:
            self.total_frame_cache_count_start = 40     #add more after testing!!!
            self.total_frame_cache_count_end = 40
        else:
            self.total_frame_cache_count_start = 20     #add more after testing!!!
            self.total_frame_cache_count_end = 20
        start_count = 0
        start_frame_cap = 0
        start_previews = {}

        start_time = time()
        
        # Collect all start frames first
        start_frames = []
        start_frame_indices = []
        start_count = 0
        start_frame_cap = 0
        
        while start_count < (self.total_frame_cache_count_start * fps):
            ret, frame = video.read()
            if ret:
                if start_frame_cap == start_count:
                    start_frames.append(frame)
                    start_frame_indices.append(start_count)
                    start_frame_cap += self.mainwindow.start_interval
                start_count += 1
            else:
                break

        # Process start frames in parallel (GPU or multi-threaded CPU)
        print(f"Processing {len(start_frames)} start frames...")
        start_previews = self.process_frame_batch_optimized(start_frames, start_frame_indices, "start_frame")
        
        end_time = time()
        print(f"Start previews processed in {end_time - start_time:.2f} seconds ({len(start_frames)} frames)")
        
        start_time = time()

        # Collect all end frames first
        end_frames = []
        end_frame_indices = []
        end_buffer = 10
        end_start = int(self.total_frames - (self.total_frame_cache_count_end * fps) - end_buffer)
        if end_start < 0:
            end_start = 0
        end_frame_cap = end_start
        
        video.set(cv2.CAP_PROP_POS_FRAMES, end_start)
        
        while end_start < (self.total_frames - end_buffer):
            ret, frame = video.read()
            if ret:
                if end_frame_cap == end_start:
                    end_frames.append(frame)
                    end_frame_indices.append(end_start)
                    end_frame_cap += self.mainwindow.end_interval
                end_start += 1
            else:
                break

        # Process end frames in parallel (GPU or multi-threaded CPU)
        print(f"Processing {len(end_frames)} end frames...")
        end_previews = self.process_frame_batch_optimized(end_frames, end_frame_indices, "end_frame")

        end_time = time()
        print(f"End previews processed in {end_time - start_time:.2f} seconds ({len(end_frames)} frames)")

        video.release()
        
        # Clear GPU memory after processing if using GPU
        if self.use_gpu:
            torch.cuda.empty_cache()
            torch.cuda.synchronize()

        reel_previews = {}
        if len(start_previews) == 0:
            self._preview_log(reel_id, split, "start previews empty; injecting fallback frame")
            start_previews = {"start_frame0": QPixmap(".\\framefail.jpg")}
        if len(end_previews) == 0:
            self._preview_log(reel_id, split, "end previews empty; injecting fallback frame")
            end_previews = {"end_frame0": QPixmap(".\\framefail.jpg")}
        reel_previews['start_previews'] = start_previews
        reel_previews['end_previews'] = end_previews
        self.mainwindow.previews_loaded += 1
        if 'preview_data' not in reel_data:
            reel_data['preview_data'] = {}
        if split == reel_data['splits']:
            reel_data['preview_loaded'] = True
        reel_data['preview_data'][split] = reel_previews
        reel_data[split] = {}
        reel_data[split]['edited'] = False


    def change_start_previews(self, direction, start_loaded_once):
        reel_preview_data = self.mainwindow.active_reel 
        reel_split_previews = reel_preview_data['preview_data'][self.mainwindow.current_split]
        reel_start_previews = reel_split_previews['start_previews']
        split_state = self._get_or_create_split_state(reel_preview_data, self.mainwindow.current_split, reel_split_previews)
        start_interval = self.mainwindow.start_interval
        min_index = split_state['start_min']
        max_page_start = split_state['start_max_page']
        step = start_interval * 5 if direction in ("prev_big", "next_big") else start_interval

        if direction in ("prev", "prev_big"):             
            split_state['start_position'] = split_state['start_position'] - step
            if split_state['start_position'] < min_index:
                split_state['start_position'] = min_index
        elif direction in ("next", "next_big"):
            if split_state['start_position'] >= max_page_start:
                return
            split_state['start_position'] = split_state['start_position'] + step
            if split_state['start_position'] > max_page_start:
                split_state['start_position'] = max_page_start
        elif direction == "reload":
            saved_start = self._saved_start_position(reel_preview_data, self.mainwindow.current_split)
            if saved_start is None:
                saved_start = split_state['start_min']
            split_state['start_position'] = saved_start

        self._sync_mainwindow_positions(split_state)
        start_gui_frame_data = self._render_strip(
            reel_start_previews,
            "start_frame",
            "sf",
            start_interval,
            split_state['start_position'],
        )
        reel_split_previews['start_gui_frame_data'] = start_gui_frame_data

    def change_end_previews(self, direction, end_loaded_once):
        reel_preview_data = self.mainwindow.active_reel
        reel_split_previews = reel_preview_data['preview_data'][self.mainwindow.current_split]
        reel_end_previews = reel_split_previews['end_previews'] 
        split_state = self._get_or_create_split_state(reel_preview_data, self.mainwindow.current_split, reel_split_previews)
        end_interval = self.mainwindow.end_interval 
        min_frame_no = split_state['end_min']
        init_frame_no = split_state['end_max_page']
        step = end_interval * 5 if direction in ("prev_big", "next_big") else end_interval

        if direction in ("prev", "prev_big"):             
            split_state['end_position'] = split_state['end_position'] - step
            if split_state['end_position'] < min_frame_no:
                split_state['end_position'] = min_frame_no
        elif direction in ("next", "next_big"):
            split_state['end_position'] = split_state['end_position'] + step
            if split_state['end_position'] > init_frame_no:
                split_state['end_position'] = init_frame_no
        elif direction == "reload":
            saved_end = self._saved_end_position(reel_preview_data, self.mainwindow.current_split)
            if saved_end is None:
                saved_end = split_state['end_max_page']
            split_state['end_position'] = saved_end

        self._sync_mainwindow_positions(split_state)
        end_gui_frame_data = self._render_strip(
            reel_end_previews,
            "end_frame",
            "ef",
            end_interval,
            split_state['end_position'],
        )
        reel_split_previews['end_gui_frame_data'] = end_gui_frame_data

    def _saved_start_position(self, reel, split):
        if 'highlight_data' in reel and split in reel['highlight_data']:
            return reel['highlight_data'][split]['preview_start_position']
        return None

    def _saved_end_position(self, reel, split):
        if 'highlight_data' in reel and split in reel['highlight_data']:
            return reel['highlight_data'][split]['preview_end_position']
        return None

    def _get_or_create_split_state(self, reel, split, reel_previews):
        start_keys = sorted(int(k.replace("start_frame", "")) for k in reel_previews['start_previews'].keys())
        end_keys = sorted(int(k.replace("end_frame", "")) for k in reel_previews['end_previews'].keys())
        if len(start_keys) == 0 or len(end_keys) == 0:
            raise ValueError(
                f"empty key set start={len(start_keys)} end={len(end_keys)} "
                f"start_keys={list(reel_previews.get('start_previews', {}).keys())[:3]} "
                f"end_keys={list(reel_previews.get('end_previews', {}).keys())[:3]}"
            )
        start_interval = self.mainwindow.start_interval
        end_interval = self.mainwindow.end_interval

        start_min = start_keys[0]
        start_max_page = max(start_min, start_keys[-1] - (start_interval * 9))
        end_min = end_keys[0]
        end_max_page = max(end_min, end_keys[-1] - (end_interval * 9))

        if 'preview_ui_state' not in reel:
            reel['preview_ui_state'] = {}
        split_state = reel['preview_ui_state'].get(split)

        if split_state is None:
            start_saved = self._saved_start_position(reel, split)
            end_saved = self._saved_end_position(reel, split)
            if start_saved is None:
                suggested_start = self._suggested_frame_for_split(reel, split, "suggested_start_frame")
                if suggested_start is not None:
                    start_saved = self._position_for_frame(
                        suggested_start,
                        start_min,
                        start_max_page,
                        start_interval,
                    )
            if end_saved is None:
                suggested_end = self._suggested_frame_for_split(reel, split, "suggested_end_frame")
                if suggested_end is not None:
                    end_saved = self._position_for_frame(
                        suggested_end,
                        end_min,
                        end_max_page,
                        end_interval,
                    )
            split_state = {
                'start_position': start_saved if start_saved is not None else 0,
                'end_position': end_saved if end_saved is not None else end_max_page,
            }
            reel['preview_ui_state'][split] = split_state

        split_state['start_min'] = start_min
        split_state['start_max_page'] = start_max_page
        split_state['end_min'] = end_min
        split_state['end_max_page'] = end_max_page

        split_state['start_position'] = min(max(split_state['start_position'], start_min), start_max_page)
        split_state['end_position'] = min(max(split_state['end_position'], end_min), end_max_page)
        return split_state

    def _sync_mainwindow_positions(self, split_state):
        self.mainwindow.preview_start_position = split_state['start_position']
        self.mainwindow.preview_end_position = split_state['end_position']
        self.mainwindow.end_loaded_once = True

    def _position_for_frame(self, frame_no, min_index, max_page_start, interval):
        if interval <= 0:
            return min(max(int(frame_no), min_index), max_page_start)
        desired = int(frame_no) - (interval * 4)
        if desired < min_index:
            desired = min_index
        page_offset = int((desired - min_index) / interval)
        page_start = min_index + (page_offset * interval)
        return min(max(page_start, min_index), max_page_start)

    def _suggested_frame_for_split(self, reel, split, key):
        split_suggestions = reel.get("split_match_suggestions", {})
        suggestion = split_suggestions.get(split)
        if suggestion is None:
            suggestion = split_suggestions.get(str(split))
        if not isinstance(suggestion, dict):
            return None
        value = suggestion.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _find_nearest_gui_label(self, gui_frame_data, expected_prefix, target_frame):
        if target_frame is None:
            return "", None
        best_label = ""
        best_frame = None
        best_distance = None
        for label_id, frame_key in gui_frame_data.items():
            if not str(frame_key).startswith(expected_prefix):
                continue
            try:
                frame_no = int(str(frame_key).replace(expected_prefix, ""))
            except (TypeError, ValueError):
                continue
            distance = abs(frame_no - target_frame)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_label = label_id
                best_frame = frame_no
        return best_label, best_frame

    def _apply_auto_split_match_suggestion(self, reel, split, reel_previews):
        split_state = reel.get(split, {})
        if split_state.get("edited"):
            return
        if "highlight_data" in reel and split in reel["highlight_data"]:
            return

        suggestion_map = reel.get("split_match_suggestions", {})
        suggestion = suggestion_map.get(split)
        if suggestion is None:
            suggestion = suggestion_map.get(str(split))
        if not isinstance(suggestion, dict):
            return

        start_target = suggestion.get("suggested_start_frame")
        end_target = suggestion.get("suggested_end_frame")
        if start_target is None and end_target is None:
            return

        start_gui = reel_previews.get("start_gui_frame_data", {})
        end_gui = reel_previews.get("end_gui_frame_data", {})
        start_label, start_frame = self._find_nearest_gui_label(start_gui, "start_frame", start_target)
        end_label, end_frame = self._find_nearest_gui_label(end_gui, "end_frame", end_target)
        if start_label == "" and end_label == "":
            return

        self.mainwindow.previews_remove_all_highlights()
        if start_label != "":
            start_widget = getattr(self.mainwindow, start_label)
            start_widget.highlighted = True
            start_widget.setStyleSheet("border: 3px solid blue;")
        if end_label != "":
            end_widget = getattr(self.mainwindow, end_label)
            end_widget.highlighted = True
            end_widget.setStyleSheet("border: 3px solid blue;")

        if "highlight_data" not in reel:
            reel["highlight_data"] = {}
        reel["highlight_data"][split] = {
            "start_trim_frame": start_label if start_label != "" else "",
            "end_trim_frame": end_label if end_label != "" else "",
            "preview_start_position": self.mainwindow.preview_start_position,
            "preview_end_position": self.mainwindow.preview_end_position,
            "auto_split_match": True,
        }

        if "trim_data" not in reel:
            reel["trim_data"] = {}
        trim_entry = {}
        if start_frame is not None:
            trim_entry["start_frame"] = int(start_frame)
        if end_frame is not None:
            trim_entry["end_frame"] = int(end_frame)
        if trim_entry:
            reel["trim_data"][split] = trim_entry

    def _render_strip(self, preview_dict, frame_prefix, widget_prefix, interval, base_position):
        gui_frame_data = {}
        for x in range(10):
            frame_index = base_position + (interval * x)
            dict_string = f"{frame_prefix}{frame_index}"
            try:
                frame = preview_dict[dict_string]
            except KeyError:
                print(f"no frame to load frame {dict_string}, available keys: {preview_dict.keys()}")
                return gui_frame_data
            object_name = f"{widget_prefix}{x}"
            gui_frame_data[object_name] = dict_string
            attribute = getattr(self.mainwindow, object_name)
            attribute.setPixmap(frame)
        return gui_frame_data
    
    def convert_to_qt(self, frame):
        """Original CPU-based Qt conversion"""
        try:
            rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_image.shape
            bytes_per_line = ch * w
            convert_to_Qt_format = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
            frame = QPixmap.fromImage(convert_to_Qt_format)
            frame = QPixmap(frame).scaledToHeight(202).scaledToWidth(270)
            return frame
        except cv2.error:
            frame = QPixmap(".\\framefail.jpg")
            return frame
