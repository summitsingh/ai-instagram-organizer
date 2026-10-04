"""Image helpers: EXIF/date extraction, hashing, conversion, prefiltering."""
import os
import base64
import shutil
import hashlib
import datetime
import concurrent.futures
from pathlib import Path
from typing import List, Optional, Tuple
from collections import defaultdict
from io import BytesIO

from PIL import Image
from PIL.ExifTags import TAGS

from .common import logger, tqdm, TQDM_AVAILABLE, THUMBNAIL_SIZE
from .config import Config

# Check for required dependencies
try:
    import imagehash
except ImportError:
    logger.error("'imagehash' is not installed. Please run: pip install imagehash")
    exit(1)

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    logger.info("HEIC/HEIF support enabled")
except ImportError:
    logger.warning("'pillow-heif' not installed. HEIC/HEIF conversion may fail.")


def get_image_hash_for_cache(image_path: str) -> str:
    """Generate a hash for caching based on file path and modification time"""
    stat = os.stat(image_path)
    cache_key = f"{image_path}_{stat.st_mtime}_{stat.st_size}"
    return hashlib.md5(cache_key.encode()).hexdigest()


def quick_quality_filter(image_path: str, config) -> bool:
    """Fast pre-filtering based on file properties"""
    try:
        # Check file size
        file_size = os.path.getsize(image_path)
        min_size_kb = getattr(config, 'min_file_size_kb', 100) * 1024
        if file_size < min_size_kb:
            return False
        
        # Check image dimensions
        with Image.open(image_path) as img:
            width, height = img.size
            min_resolution = getattr(config, 'min_resolution', [800, 600])
            min_width = min_resolution[0] if len(min_resolution) > 0 else 800
            min_height = min_resolution[1] if len(min_resolution) > 1 else 600
            
            if width < min_width or height < min_height:
                return False
                
        return True
    except Exception:
        return False


def get_exif_datetime(image_path: str) -> datetime.datetime:
    """Extract creation datetime from image EXIF data"""
    try:
        with Image.open(image_path) as img:
            exif_data = img._getexif()
            if exif_data:
                for tag, value in exif_data.items():
                    tag_name = TAGS.get(tag, tag)
                    if tag_name in ['DateTimeOriginal', 'DateTime', 'DateTimeDigitized']:
                        try:
                            return datetime.datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                        except ValueError:
                            continue
    except Exception as e:
        logger.debug(f"Could not read EXIF data from {image_path}: {e}")
    
    return datetime.datetime.fromtimestamp(os.path.getmtime(image_path))


def fast_prefilter_images(image_paths: List[str], config: Config) -> List[str]:
    """Quick pre-filter based on file size and basic properties"""
    if not config.enable_prefilter or len(image_paths) < 100:
        return image_paths
    
    logger.info("Pre-filtering images by file size and metadata...")
    
    size_groups = defaultdict(list)
    
    # Create progress bar if tqdm is available
    if TQDM_AVAILABLE:
        iterator = tqdm(image_paths, desc="Pre-filtering by size", unit="img")
    else:
        iterator = image_paths
        logger.info("Processing file sizes...")
    
    processed_count = 0
    for path in iterator:
        try:
            size = os.path.getsize(path)
            # Group by approximate size (within 10% tolerance for pre-filter)
            size_key = size // max(1, int(size * 0.1))
            size_groups[size_key].append(path)
            
            if TQDM_AVAILABLE:
                iterator.set_postfix({"Size": f"{size//1024}KB", "Groups": len(size_groups)})
            
            processed_count += 1
            if not TQDM_AVAILABLE and processed_count % 100 == 0:
                logger.info(f"Processed {processed_count}/{len(image_paths)} files...")
                
        except Exception as e:
            logger.debug(f"Could not get size for {path}: {e}")
            # Add to a default group for files we can't read
            size_groups[0].append(path)
    
    # Count groups that need hash comparison
    groups_needing_hash = sum(1 for group in size_groups.values() if len(group) > 1)
    total_in_groups = sum(len(group) for group in size_groups.values() if len(group) > 1)
    
    logger.info(f"Pre-filter results:")
    logger.info(f"  - {len(size_groups)} size groups created")
    logger.info(f"  - {groups_needing_hash} groups need hash comparison")
    logger.info(f"  - {total_in_groups} images need detailed analysis")
    logger.info(f"  - {len(image_paths) - total_in_groups} images are unique by size")
    
    return image_paths


def compute_image_hash(path_and_config: tuple) -> tuple:
    """Compute hash for a single image - designed for parallel processing"""
    path, thumbnail_size, hash_size = path_and_config
    try:
        with Image.open(path) as img:
            # Resize image for faster hashing
            img.thumbnail((thumbnail_size, thumbnail_size), Image.Resampling.LANCZOS)
            img_hash = imagehash.phash(img, hash_size=hash_size)
            return path, img_hash, True, None
    except Exception as e:
        return path, None, False, str(e)


def filter_similar_images(image_paths: List[str], threshold: int = 5, config: Config = None) -> List[str]:
    """Find and remove visually similar images using optimized perceptual hashing"""
    
    # Use config settings or defaults
    if config:
        parallel_workers = config.parallel_workers
        thumbnail_size = config.thumbnail_size
        hash_size = config.hash_size
        enable_prefilter = config.enable_prefilter
    else:
        parallel_workers = 4
        thumbnail_size = 256
        hash_size = 8
        enable_prefilter = True
    
    logger.info(f"Filtering {len(image_paths)} images for similarity (threshold: {threshold})")
    logger.info(f"Using {parallel_workers} workers, {thumbnail_size}px thumbnails, hash_size={hash_size}")
    
    # Step 0: Optional pre-filtering
    if enable_prefilter and len(image_paths) > 200:
        image_paths = fast_prefilter_images(image_paths, config)
    
    # Step 1: Generate hashes with progress tracking and parallel processing
    logger.info("Computing image hashes...")
    hashes = {}
    failed_paths = []
    
    # Prepare arguments for parallel processing
    hash_args = [(path, thumbnail_size, hash_size) for path in image_paths]
    
    # Use ThreadPoolExecutor for I/O bound operations
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        # Submit all tasks
        future_to_path = {executor.submit(compute_image_hash, args): args[0] for args in hash_args}
        
        # Process results with progress bar
        if TQDM_AVAILABLE:
            progress_bar = tqdm(total=len(image_paths), desc="Computing hashes", unit="img")
        else:
            progress_bar = None
            logger.info(f"Computing hashes for {len(image_paths)} images...")
        
        completed = 0
        for future in concurrent.futures.as_completed(future_to_path):
            path, img_hash, success, error = future.result()
            completed += 1
            
            if success and img_hash is not None:
                hashes[path] = img_hash
                if progress_bar:
                    progress_bar.set_postfix({
                        "Success": len(hashes),
                        "Failed": len(failed_paths),
                        "Current": os.path.basename(path)[:15]
                    })
                elif completed % 50 == 0:
                    logger.info(f"Processed {completed}/{len(image_paths)} images...")
            else:
                failed_paths.append(path)
                logger.debug(f"Failed to hash {os.path.basename(path)}: {error}")
                if progress_bar:
                    progress_bar.set_postfix({
                        "Success": len(hashes),
                        "Failed": len(failed_paths),
                        "Error": os.path.basename(path)[:15]
                    })
            
            if progress_bar:
                progress_bar.update(1)
        
        if progress_bar:
            progress_bar.close()
    
    logger.info(f"Hash computation complete: {len(hashes)} successful, {len(failed_paths)} failed")
    
    # Step 2: Group similar images using optimized comparison
    logger.info("Finding similar image groups...")
    
    # Convert to list for indexed access
    hash_items = list(hashes.items())
    unique_paths = []
    processed = set()
    skipped_count = 0
    similar_groups_found = 0
    
    if TQDM_AVAILABLE:
        progress_bar = tqdm(total=len(hash_items), desc="Finding duplicates", unit="img")
    else:
        progress_bar = None
        logger.info(f"Comparing {len(hash_items)} image hashes...")
    
    for i, (path1, hash1) in enumerate(hash_items):
        if path1 in processed:
            if progress_bar:
                progress_bar.update(1)
            continue
        
        # This image is unique so far
        unique_paths.append(path1)
        processed.add(path1)
        
        # Find all similar images to this one
        similar_group = [path1]
        
        # Only compare with remaining images (optimization)
        for j in range(i + 1, len(hash_items)):
            path2, hash2 = hash_items[j]
            
            if path2 in processed:
                continue
            
            # Check similarity
            hash_diff = abs(hash1 - hash2)
            if hash_diff <= threshold:
                similar_group.append(path2)
                processed.add(path2)
                skipped_count += 1
        
        # Log similar groups
        if len(similar_group) > 1:
            similar_groups_found += 1
            group_names = [os.path.basename(p) for p in similar_group[:3]]
            if len(similar_group) > 3:
                group_names.append(f"...+{len(similar_group)-3} more")
            logger.info(f"Similar group #{similar_groups_found} ({len(similar_group)} images): {', '.join(group_names)}")
        
        if progress_bar:
            progress_bar.set_postfix({
                "Unique": len(unique_paths), 
                "Skipped": skipped_count,
                "Groups": similar_groups_found,
                "Current": os.path.basename(path1)[:15]
            })
            progress_bar.update(1)
        elif i % 50 == 0:
            logger.info(f"Processed {i}/{len(hash_items)} comparisons...")
    
    if progress_bar:
        progress_bar.close()
    
    # Include failed images (they couldn't be processed for similarity)
    unique_paths.extend(failed_paths)
    
    # Final summary
    logger.info("=" * 50)
    logger.info("SIMILARITY FILTERING COMPLETE")
    logger.info("=" * 50)
    logger.info(f"Original images: {len(image_paths)}")
    logger.info(f"Successfully processed: {len(hashes)}")
    logger.info(f"Failed to process: {len(failed_paths)}")
    logger.info(f"Similar groups found: {similar_groups_found}")
    logger.info(f"Similar images skipped: {skipped_count}")
    logger.info(f"Unique images kept: {len(unique_paths)}")
    logger.info(f"Space saved: {skipped_count} duplicate images removed")
    logger.info("=" * 50)
    
    return unique_paths


def convert_and_prepare_images(source_dir: str, temp_dir: str, all_source_paths: List[str], quality: int = 95) -> Tuple[str, List[str]]:
    """Convert HEIC images to JPG and prepare final image paths (optimized - no unnecessary conversions)"""
    logger.info("Preparing images and converting HEIC files...")
    
    # Create unique temp directory based on source folder to avoid conflicts
    import hashlib
    source_hash = hashlib.md5(source_dir.encode()).hexdigest()[:8]
    unique_temp_dir = f"{temp_dir}_{source_hash}"
    
    # Clean and recreate temp directory to ensure no old files
    if os.path.exists(unique_temp_dir):
        import shutil
        logger.info(f"Cleaning existing temp directory: {unique_temp_dir}")
        shutil.rmtree(unique_temp_dir)
    
    Path(unique_temp_dir).mkdir(parents=True, exist_ok=True)
    logger.info(f"Using temp directory: {unique_temp_dir}")
    
    converted_count = 0
    used_original_count = 0
    error_count = 0
    final_image_paths = []
    
    # Separate HEIC files from others
    heic_files = []
    ready_files = []
    
    for original_path in all_source_paths:
        filename = os.path.basename(original_path)
        base_name, extension = os.path.splitext(filename)
        
        if extension.lower() in ['.heic', '.heif']:
            heic_files.append(original_path)
        else:
            # JPG, JPEG, PNG files can be used directly
            ready_files.append(original_path)
    
    logger.info(f"Found {len(heic_files)} HEIC/HEIF files to convert, {len(ready_files)} files ready to use")
    
    # Add ready files directly to final paths (no conversion needed)
    final_image_paths.extend(ready_files)
    used_original_count = len(ready_files)
    
    # Only convert HEIC/HEIF files
    if heic_files:
        if TQDM_AVAILABLE:
            progress_bar = tqdm(heic_files, desc="Converting HEIC files", unit="img")
        else:
            progress_bar = heic_files
            logger.info(f"Converting {len(heic_files)} HEIC/HEIF files...")
        
        for i, original_path in enumerate(progress_bar if TQDM_AVAILABLE else heic_files):
            filename = os.path.basename(original_path)
            base_name, extension = os.path.splitext(filename)
            target_filename = base_name + ".jpg"
            destination_path = os.path.join(unique_temp_dir, target_filename)
            
            try:
                with Image.open(original_path) as image:
                    rgb_image = image.convert("RGB")
                    rgb_image.save(destination_path, "JPEG", quality=quality)
                    final_image_paths.append(destination_path)
                    converted_count += 1
                    
            except Exception as e:
                logger.error(f"Could not convert {filename}: {e}")
                error_count += 1
            
            if TQDM_AVAILABLE:
                progress_bar.set_postfix({
                    "Converted": converted_count,
                    "Errors": error_count
                })
            elif not TQDM_AVAILABLE and (i + 1) % 10 == 0:
                logger.info(f"Converted {i + 1}/{len(heic_files)} HEIC files...")
        
        if TQDM_AVAILABLE:
            progress_bar.close()
    
    logger.info(f"Image preparation complete:")
    logger.info(f"  - Used original files (JPG/PNG): {used_original_count}")
    logger.info(f"  - Converted (HEIC→JPG): {converted_count}")
    logger.info(f"  - Errors: {error_count}")
    logger.info(f"  - Total ready images: {len(final_image_paths)}")
    
    return unique_temp_dir, final_image_paths


def cleanup_temp_directory(temp_dir: str, keep_files: bool = False) -> None:
    """Clean up temporary directory after processing"""
    if not keep_files and os.path.exists(temp_dir):
        import shutil
        try:
            shutil.rmtree(temp_dir)
            logger.info(f"Cleaned up temporary directory: {temp_dir}")
        except Exception as e:
            logger.warning(f"Could not clean up temp directory {temp_dir}: {e}")
    elif keep_files:
        logger.info(f"Keeping temporary files in: {temp_dir}")


def encode_image_to_base64(image_path: str, config: Config = None) -> Optional[str]:
    """Encode image to base64 string with optimized settings"""
    try:
        with Image.open(image_path) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Use faster thumbnail size if config available
            if config and hasattr(config, 'enable_fast_mode') and config.enable_fast_mode:
                thumbnail_size = (config.fast_thumbnail_size, config.fast_thumbnail_size)
                quality = config.fast_jpeg_quality
            else:
                thumbnail_size = THUMBNAIL_SIZE
                quality = 85
            
            img.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
            buffered = BytesIO()
            img.save(buffered, format="JPEG", quality=quality)
            
            return base64.b64encode(buffered.getvalue()).decode('utf-8')
            
    except Exception as e:
        logger.error(f"Could not encode image {image_path}: {e}")
        return None
