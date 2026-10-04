"""Analysis orchestration, filtering, scoring, selection, and post building."""

import os
import json
import time
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from .common import (
    logger,
    tqdm,
    TQDM_AVAILABLE,
    SUPPORTED_FORMATS,
    _analysis_cache,
    _cache_lock,
    HashtagOptimizer,
    create_platform_variants,
)
from .config import Config
from .images import (
    encode_image_to_base64,
    get_exif_datetime,
    get_image_hash_for_cache,
    quick_quality_filter,
)
from .providers.gemini import analyze_with_gemini, analyze_images_gemini_optimized
from .providers.llama import analyze_with_llama, analyze_images_llama_optimized
from .providers.ollama import analyze_with_ollama
from .providers.gemini import generate_content_with_gemini
from .providers.llama import generate_content_with_llama
from .providers.ollama import generate_content_with_ollama


def filter_contextually_similar_images(
    analyzed_data: List[Dict], config: Config
) -> List[Dict]:
    """Filter images with similar context using AI analysis"""
    if not config.enable_contextual_filtering:
        return analyzed_data

    logger.info(f"Filtering {len(analyzed_data)} images for contextual similarity")

    # Group images by similar context
    context_groups = []
    processed_indices = set()

    for i, photo1 in enumerate(analyzed_data):
        if i in processed_indices:
            continue

        # Start a new context group
        current_group = [photo1]
        processed_indices.add(i)

        # Find similar context photos
        for j, photo2 in enumerate(analyzed_data[i + 1 :], i + 1):
            if j in processed_indices:
                continue

            similarity_score = calculate_contextual_similarity(photo1, photo2)

            if similarity_score >= config.contextual_similarity_threshold:
                current_group.append(photo2)
                processed_indices.add(j)

        context_groups.append(current_group)

    # Select best photo from each context group
    filtered_photos = []
    skipped_count = 0

    for group in context_groups:
        if len(group) == 1:
            filtered_photos.append(group[0])
        else:
            # Select best photo from the group
            best_photo = select_best_from_context_group(group, config)
            filtered_photos.append(best_photo)
            skipped_count += len(group) - 1

            # Get category from enhanced or analysis data
            category = best_photo.get("enhanced", {}).get("category") or best_photo[
                "analysis"
            ].get("category", "unknown")
            logger.debug(
                f"Context group: kept 1/{len(group)} photos - {category} scene"
            )

    logger.info(
        f"Contextual filtering: kept {len(filtered_photos)} photos, skipped {skipped_count} contextually similar"
    )
    return filtered_photos


def calculate_contextual_similarity(photo1: Dict, photo2: Dict) -> float:
    """Calculate contextual similarity between two photos based on AI analysis"""
    analysis1 = photo1.get("analysis", {})
    analysis2 = photo2.get("analysis", {})

    # Category similarity (40% weight)
    category_score = (
        1.0 if analysis1.get("category") == analysis2.get("category") else 0.0
    )
    subcategory_score = (
        1.0 if analysis1.get("subcategory") == analysis2.get("subcategory") else 0.0
    )

    # Location/setting similarity (30% weight)
    location1 = (analysis1.get("location") or "").lower()
    location2 = (analysis2.get("location") or "").lower()
    location_score = calculate_text_similarity(location1, location2)

    # Mood similarity (20% weight)
    mood_score = 1.0 if analysis1.get("mood") == analysis2.get("mood") else 0.0

    # People count similarity (10% weight)
    people1 = analysis1.get("people_present", "0")
    people2 = analysis2.get("people_present", "0")
    people_score = 1.0 if people1 == people2 else 0.0

    # Weighted similarity score
    similarity = (
        category_score * 0.25
        + subcategory_score * 0.15
        + location_score * 0.30
        + mood_score * 0.20
        + people_score * 0.10
    )

    return similarity


def calculate_text_similarity(text1: str, text2: str) -> float:
    """Calculate similarity between two text descriptions"""
    if not text1 or not text2:
        return 0.0

    # Simple word overlap similarity
    words1 = set(text1.split())
    words2 = set(text2.split())

    if not words1 or not words2:
        return 0.0

    intersection = words1.intersection(words2)
    union = words1.union(words2)

    return len(intersection) / len(union) if union else 0.0


def select_best_from_context_group(group: List[Dict], config: Config) -> Dict:
    """Select the best photo from a group of contextually similar photos"""

    # Helper function to get composite score from either enhanced or analysis data
    def get_composite_score(photo):
        if "enhanced" in photo and "composite_score" in photo["enhanced"]:
            return photo["enhanced"]["composite_score"]
        return photo["analysis"].get("composite_score", 0)

    # Strategy 1: Highest composite score (default)
    if config.contextual_selection_strategy == "highest_score":
        return max(group, key=get_composite_score)

    # Strategy 2: Most unique (lowest similarity to others)
    elif config.contextual_selection_strategy == "most_unique":
        best_photo = None
        lowest_avg_similarity = float("inf")

        for candidate in group:
            similarities = []
            for other in group:
                if candidate != other:
                    sim = calculate_contextual_similarity(candidate, other)
                    similarities.append(sim)

            avg_similarity = (
                sum(similarities) / len(similarities) if similarities else 0
            )
            if avg_similarity < lowest_avg_similarity:
                lowest_avg_similarity = avg_similarity
                best_photo = candidate

        return best_photo or group[0]

    # Strategy 3: Best technical quality
    elif config.contextual_selection_strategy == "best_technical":
        return max(group, key=lambda p: p["analysis"].get("technical_score", 0))

    # Strategy 4: Highest engagement potential
    elif config.contextual_selection_strategy == "best_engagement":
        return max(group, key=lambda p: p["analysis"].get("engagement_score", 0))

    # Default fallback
    return max(group, key=get_composite_score)


def analyze_image_with_ai(image_path: str, config: Config) -> Optional[Dict]:
    """Analyze image using configured AI provider with caching"""

    # Check cache first if enabled
    if config.enable_caching:
        cache_key = get_image_hash_for_cache(image_path)
        with _cache_lock:
            if cache_key in _analysis_cache:
                cache_entry = _analysis_cache[cache_key]
                # Check if cache is still valid
                cache_age_hours = (time.time() - cache_entry["timestamp"]) / 3600
                if cache_age_hours < config.cache_duration_hours:
                    logger.debug(
                        f"Using cached analysis for {os.path.basename(image_path)}"
                    )
                    return cache_entry["result"]
                else:
                    # Remove expired cache entry
                    del _analysis_cache[cache_key]

    logger.info(f"Analyzing: {os.path.basename(image_path)} using {config.ai_provider}")

    base64_image = encode_image_to_base64(image_path, config)
    if not base64_image:
        return None

    # Analyze with retries
    result = None
    for attempt in range(config.ai_max_retries):
        try:
            if config.ai_provider == "gemini":
                result = analyze_with_gemini(base64_image, config)
            elif config.ai_provider == "llama":
                result = analyze_with_llama(base64_image, config)
            else:
                result = analyze_with_ollama(base64_image, config)

            if result:
                break

        except Exception as e:
            logger.warning(
                f"Analysis attempt {attempt + 1} failed for {os.path.basename(image_path)}: {e}"
            )
            if attempt < config.ai_max_retries - 1:
                time.sleep(1 * (attempt + 1))  # Exponential backoff

    # Cache the result if successful and caching is enabled
    if result and config.enable_caching:
        cache_key = get_image_hash_for_cache(image_path)
        with _cache_lock:
            _analysis_cache[cache_key] = {"result": result, "timestamp": time.time()}

    return result


def analyze_images_parallel(image_paths: List[str], config: Config) -> List[Dict]:
    """Analyze multiple images in parallel with progress tracking and provider-specific optimizations"""
    logger.info(
        f"Starting parallel AI analysis of {len(image_paths)} images using {config.ai_parallel_workers} workers"
    )

    # Pre-filter images for quality if fast mode is enabled
    if config.enable_fast_mode:
        logger.info("Pre-filtering images for quality...")
        filtered_paths = []
        for path in image_paths:
            if quick_quality_filter(path, config):
                filtered_paths.append(path)

        logger.info(
            f"Pre-filter: {len(filtered_paths)}/{len(image_paths)} images passed quality check"
        )
        image_paths = filtered_paths

    # Use multi-provider processing for maximum speed
    if config.ai_provider == "multi":
        logger.info("Using multi-provider processing for maximum speed")
        return analyze_images_multi_provider(image_paths, config)

    # Use optimized processing for Llama API
    elif config.ai_provider == "llama" and len(image_paths) > 5:
        logger.info("Using optimized Llama batch processing")
        return analyze_images_llama_optimized(image_paths, config)

    # Use Gemini batch processing if available
    elif config.ai_provider == "gemini" and len(image_paths) > 8:
        logger.info("Using optimized Gemini batch processing")
        return analyze_images_gemini_optimized(image_paths, config)

    # Default parallel processing for other providers or small batches
    results = []
    successful_analyses = 0

    with ThreadPoolExecutor(max_workers=config.ai_parallel_workers) as executor:
        # Submit all tasks
        future_to_path = {
            executor.submit(analyze_image_with_ai, path, config): path
            for path in image_paths
        }

        # Process completed tasks with progress tracking
        if TQDM_AVAILABLE:
            progress_bar = tqdm(
                as_completed(future_to_path),
                total=len(future_to_path),
                desc="AI Analysis",
                unit="img",
            )
        else:
            progress_bar = as_completed(future_to_path)

        for future in progress_bar:
            path = future_to_path[future]
            try:
                result = future.result()
                if result:
                    results.append(
                        {
                            "path": path,
                            "analysis": result,
                            "datetime": get_exif_datetime(path),
                        }
                    )
                    successful_analyses += 1

                    if TQDM_AVAILABLE:
                        progress_bar.set_postfix(
                            {
                                "Success": successful_analyses,
                                "Instagram-worthy": sum(
                                    1
                                    for r in results
                                    if r["analysis"].get("instagram_worthy", False)
                                ),
                                "Quality": (
                                    f"{sum(r['analysis'].get('composite_score', 0) for r in results) / len(results):.1f}/10"
                                    if results
                                    else "0/10"
                                ),
                            }
                        )

            except Exception as e:
                logger.error(f"Failed to analyze {os.path.basename(path)}: {e}")

    logger.info(
        f"Successfully analyzed {successful_analyses}/{len(image_paths)} images"
    )
    return results


def analyze_images_multi_provider(image_paths: List[str], config: Config) -> List[Dict]:
    """Multi-provider analysis with load balancing and failover"""
    if config.ai_provider == "multi":
        return analyze_images_with_load_balancing(image_paths, config)
    elif config.ai_provider == "llama":
        return analyze_images_llama_optimized(image_paths, config)
    elif config.ai_provider == "gemini":
        return analyze_images_gemini_optimized(image_paths, config)
    else:
        return analyze_images_llama_optimized(image_paths, config)


def analyze_images_with_load_balancing(
    image_paths: List[str], config: Config
) -> List[Dict]:
    """Load balance across multiple AI providers for maximum speed"""
    multi_config = config.config.get("multi_provider", {})
    providers = multi_config.get("providers", ["llama", "gemini"])

    logger.info(
        f"Multi-provider processing: {len(image_paths)} images across {len(providers)} providers"
    )

    # Split images across providers
    chunks = []
    chunk_size = len(image_paths) // len(providers)

    for i, provider in enumerate(providers):
        start_idx = i * chunk_size
        if i == len(providers) - 1:  # Last provider gets remaining images
            end_idx = len(image_paths)
        else:
            end_idx = start_idx + chunk_size

        if start_idx < len(image_paths):
            chunks.append((provider, image_paths[start_idx:end_idx]))

    results = []

    # Process chunks in parallel
    with ThreadPoolExecutor(max_workers=len(providers)) as executor:
        futures = []

        for provider, chunk_paths in chunks:
            if provider == "llama":
                future = executor.submit(
                    analyze_images_llama_optimized, chunk_paths, config
                )
            elif provider == "gemini":
                future = executor.submit(
                    analyze_images_gemini_optimized, chunk_paths, config
                )
            else:
                future = executor.submit(
                    analyze_images_llama_optimized, chunk_paths, config
                )

            futures.append((provider, future))

        # Collect results
        for provider, future in futures:
            try:
                chunk_results = future.result(
                    timeout=300
                )  # 5 minute timeout per provider
                results.extend(chunk_results)
                logger.info(
                    f"{provider} provider completed: {len(chunk_results)} images"
                )
            except Exception as e:
                logger.error(f"{provider} provider failed: {e}")

    logger.info(
        f"Multi-provider analysis complete: {len(results)} total images processed"
    )
    return results


def generate_post_content(post_images: List[str], config: Config) -> Optional[Dict]:
    """Generate captions and hashtags for a set of images"""
    logger.info(
        f"Generating content for {len(post_images)} images using {config.ai_provider}"
    )

    base64_images = []
    for img_path in post_images:
        encoded = encode_image_to_base64(img_path)
        if encoded:
            base64_images.append(encoded)

    if not base64_images:
        return None

    if config.ai_provider == "gemini":
        return generate_content_with_gemini(base64_images, config)
    elif config.ai_provider == "llama":
        return generate_content_with_llama(base64_images, config)
    else:
        return generate_content_with_ollama(base64_images, config)


def save_post_content(
    post_dir: str,
    content: Dict,
    post_name: str,
    config: Config,
    images: List[str] = None,
) -> None:
    """Save post content to files"""
    try:
        # Optimize hashtags if enabled
        if config.enable_hashtag_optimization and images:
            optimizer = HashtagOptimizer()
            theme = content.get("post_theme", "")
            location = content.get("location", "")
            optimized_hashtags = optimizer.optimize_hashtags(
                content.get("hashtags", []), theme, location
            )
            content["hashtags"] = optimized_hashtags

        with open(os.path.join(post_dir, "captions.txt"), "w", encoding="utf-8") as f:
            f.write(f"=== {post_name.upper()} ===\n\n")

            if "post_theme" in content:
                f.write(f"THEME: {content['post_theme']}\n\n")

            f.write("--- CAPTION OPTIONS ---\n\n")
            for c_idx, cap in enumerate(content.get("caption_options", []), 1):
                f.write(f"{c_idx}. {cap}\n\n")

            f.write("--- HASHTAGS ---\n\n")
            hashtags = content.get("hashtags", [])
            formatted_hashtags = " ".join([f"#{tag.strip('#')}" for tag in hashtags])
            f.write(formatted_hashtags)

        # Create multi-platform variants if enabled
        if config.enable_multi_platform and images:
            create_platform_variants(post_dir, images, content)

        logger.info(f"Saved content for {post_name}")

    except Exception as e:
        logger.error(f"Could not save content for {post_name}: {e}")


def organize_photos_enhanced(analyzed_data: List[Dict], config: Config) -> None:
    """Enhanced photo organization with smart categorization and diversity optimization"""
    logger.info("Organizing photos with enhanced algorithm...")

    # Categorize photos by quality tiers
    photo_tiers = {
        "premium": [],
        "excellent": [],
        "good": [],
        "average": [],
        "poor": [],
    }

    for data in analyzed_data:
        if data and data["analysis"]:
            tier = data["analysis"].get("instagram_tier", "average")
            composite_score = data["analysis"].get("composite_score", 0)

            # Enhanced categorization
            data["enhanced"] = {
                "tier": tier,
                "composite_score": composite_score,
                "category": data["analysis"].get("category", "unknown"),
                "mood": data["analysis"].get("mood", "neutral"),
                "setting": determine_setting(data["analysis"]),
                "time_of_day": determine_time_of_day(data["analysis"]),
                "people_count": determine_people_count(data["analysis"]),
            }

            photo_tiers[tier].append(data)

    # Log tier distribution
    for tier, photos in photo_tiers.items():
        logger.info(f"{tier.capitalize()} photos: {len(photos)}")

    # Only use premium, excellent, and good photos
    worthy_photos = (
        photo_tiers["premium"] + photo_tiers["excellent"] + photo_tiers["good"]
    )

    if not worthy_photos:
        logger.warning("No high-quality Instagram-worthy photos found.")
        return

    logger.info(f"Found {len(worthy_photos)} high-quality photos for posting")

    Path(config.output_folder).mkdir(parents=True, exist_ok=True)

    # Strategy 1: Premium showcase posts (best of the best)
    premium_posts = create_premium_posts(photo_tiers["premium"], config)

    # Strategy 2: Diverse excellence posts (mixed high-quality)
    diverse_posts = create_diverse_posts(
        photo_tiers["excellent"] + photo_tiers["premium"], config
    )

    # Strategy 3: Theme-based posts (category groupings)
    theme_posts = create_theme_posts(worthy_photos, config)

    # Strategy 4: Chronological posts (remaining photos)
    remaining_photos = get_remaining_photos(
        worthy_photos, premium_posts + diverse_posts + theme_posts
    )
    chrono_posts = create_chronological_posts(remaining_photos, config)

    # Save all posts
    all_posts = {
        "Premium_Showcase": premium_posts,
        "Diverse_Excellence": diverse_posts,
        "Theme_Based": theme_posts,
        "Chronological": chrono_posts,
    }

    total_posts = 0
    for post_type, posts in all_posts.items():
        if posts:
            save_post_collection(posts, post_type, config)
            total_posts += len(posts)

    # Generate analytics report
    generate_analytics_report(worthy_photos, all_posts, config)

    logger.info(
        f"Enhanced organization complete! Created {total_posts} optimized posts"
    )


def determine_setting(analysis: Dict) -> str:
    """Determine photo setting from analysis"""
    location = (analysis.get("location") or "").lower()

    if any(
        word in location
        for word in ["indoor", "inside", "room", "kitchen", "restaurant"]
    ):
        return "indoor"
    elif any(word in location for word in ["city", "urban", "street", "building"]):
        return "urban"
    elif any(
        word in location for word in ["nature", "forest", "mountain", "beach", "lake"]
    ):
        return "nature"
    else:
        return "outdoor"


def determine_time_of_day(analysis: Dict) -> str:
    """Determine time of day from analysis"""
    strengths = " ".join(analysis.get("strengths") or []).lower()
    location = (analysis.get("location") or "").lower()
    time_indicators = (analysis.get("time_of_day_indicators") or "").lower()

    combined_text = strengths + location + time_indicators

    if any(word in combined_text for word in ["golden hour", "sunset", "sunrise"]):
        return "golden_hour"
    elif any(word in combined_text for word in ["blue hour", "twilight", "dusk"]):
        return "blue_hour"
    elif any(word in combined_text for word in ["night", "dark", "evening"]):
        return "night"
    elif any(word in combined_text for word in ["bright", "midday", "noon"]):
        return "midday"
    else:
        return "unknown"


def determine_people_count(analysis: Dict) -> int:
    """Determine number of people from analysis"""
    people_present = analysis.get("people_present", "0")

    if isinstance(people_present, str):
        if "6+" in people_present or "many" in people_present.lower():
            return 6
        elif "2-5" in people_present:
            return 3
        elif "1" in people_present:
            return 1
        else:
            return 0

    return int(people_present) if isinstance(people_present, (int, float)) else 0


def create_premium_posts(
    premium_photos: List[Dict], config: Config
) -> List[List[Dict]]:
    """Create posts from premium photos only"""
    if len(premium_photos) < config.post_size:
        return []

    # Sort by composite score
    premium_photos.sort(key=lambda x: x["enhanced"]["composite_score"], reverse=True)

    posts = []
    max_premium_posts = min(
        3, len(premium_photos) // config.post_size
    )  # Max 3 premium posts

    for i in range(max_premium_posts):
        start_idx = i * config.post_size
        end_idx = start_idx + config.post_size
        post_photos = premium_photos[start_idx:end_idx]

        if len(post_photos) == config.post_size:
            posts.append(post_photos)

    logger.info(f"Created {len(posts)} premium showcase posts")
    return posts


def create_diverse_posts(photos: List[Dict], config: Config) -> List[List[Dict]]:
    """Create posts optimized for diversity"""
    if len(photos) < config.post_size:
        return []

    posts = []
    available_photos = photos.copy()
    max_diverse_posts = min(5, len(photos) // config.post_size)  # Max 5 diverse posts

    for _ in range(max_diverse_posts):
        if len(available_photos) < config.post_size:
            break

        post = select_diverse_photo_set(available_photos, config.post_size)
        if post:
            posts.append(post)
            # Remove selected photos
            available_photos = [p for p in available_photos if p not in post]

    logger.info(f"Created {len(posts)} diverse excellence posts")
    return posts


def select_diverse_photo_set(photos: List[Dict], post_size: int) -> List[Dict]:
    """Select a diverse set of photos for one post"""
    if len(photos) < post_size:
        return []

    # Start with highest scoring photo
    selected = [max(photos, key=lambda p: p["enhanced"]["composite_score"])]
    remaining = [p for p in photos if p != selected[0]]

    # Select remaining photos to maximize diversity
    while len(selected) < post_size and remaining:
        best_photo = None
        best_diversity_score = -1

        for candidate in remaining:
            diversity_score = calculate_diversity_score(selected, candidate)
            if diversity_score > best_diversity_score:
                best_diversity_score = diversity_score
                best_photo = candidate

        if best_photo:
            selected.append(best_photo)
            remaining.remove(best_photo)

    return selected


def calculate_diversity_score(selected_photos: List[Dict], candidate: Dict) -> float:
    """Calculate how much diversity a candidate photo adds"""
    if not selected_photos:
        return candidate["enhanced"]["composite_score"]

    # Category diversity
    selected_categories = [p["enhanced"]["category"] for p in selected_photos]
    category_diversity = (
        1.0 if candidate["enhanced"]["category"] not in selected_categories else 0.3
    )

    # Mood diversity
    selected_moods = [p["enhanced"]["mood"] for p in selected_photos]
    mood_diversity = 1.0 if candidate["enhanced"]["mood"] not in selected_moods else 0.5

    # Time diversity
    selected_times = [p["enhanced"]["time_of_day"] for p in selected_photos]
    time_diversity = (
        1.0 if candidate["enhanced"]["time_of_day"] not in selected_times else 0.4
    )

    # Setting diversity
    selected_settings = [p["enhanced"]["setting"] for p in selected_photos]
    setting_diversity = (
        1.0 if candidate["enhanced"]["setting"] not in selected_settings else 0.6
    )

    # Quality consistency (prefer similar quality levels)
    avg_quality = sum(p["enhanced"]["composite_score"] for p in selected_photos) / len(
        selected_photos
    )
    quality_consistency = (
        1.0 - abs(candidate["enhanced"]["composite_score"] - avg_quality) / 10.0
    )

    # Weighted diversity score
    diversity_weights = {
        "category_diversity": 0.3,
        "mood_diversity": 0.2,
        "time_diversity": 0.2,
        "setting_diversity": 0.15,
        "quality_consistency": 0.15,
    }

    diversity_score = (
        category_diversity * diversity_weights["category_diversity"]
        + mood_diversity * diversity_weights["mood_diversity"]
        + time_diversity * diversity_weights["time_diversity"]
        + setting_diversity * diversity_weights["setting_diversity"]
        + quality_consistency * diversity_weights["quality_consistency"]
    )

    # Boost by photo quality
    return diversity_score * (candidate["enhanced"]["composite_score"] / 10.0)


def create_theme_posts(photos: List[Dict], config: Config) -> List[List[Dict]]:
    """Create posts based on themes/categories"""

    # Group by primary category
    category_groups = defaultdict(list)
    for photo in photos:
        category = photo["enhanced"]["category"]
        category_groups[category].append(photo)

    theme_posts = []

    # Create posts for categories with enough photos
    for category, category_photos in category_groups.items():
        if len(category_photos) >= config.post_size:
            # Sort by quality
            category_photos.sort(
                key=lambda p: p["enhanced"]["composite_score"], reverse=True
            )

            # Create one theme post per category (best photos only)
            post_photos = category_photos[: config.post_size]
            theme_posts.append(post_photos)

    logger.info(f"Created {len(theme_posts)} theme-based posts")
    return theme_posts


def create_chronological_posts(photos: List[Dict], config: Config) -> List[List[Dict]]:
    """Create chronological posts from remaining photos"""
    if len(photos) < config.post_size:
        return []

    # Sort by date
    photos.sort(key=lambda p: p["datetime"])

    posts = []
    for i in range(0, len(photos), config.post_size):
        post_photos = photos[i : i + config.post_size]
        if len(post_photos) == config.post_size:
            posts.append(post_photos)

    logger.info(f"Created {len(posts)} chronological posts")
    return posts


def get_remaining_photos(
    all_photos: List[Dict], used_posts: List[List[Dict]]
) -> List[Dict]:
    """Get photos not used in any post yet"""
    used_photos = set()
    for post in used_posts:
        for photo in post:
            used_photos.add(photo["path"])

    return [photo for photo in all_photos if photo["path"] not in used_photos]


def save_post_collection(
    posts: List[List[Dict]], post_type: str, config: Config
) -> None:
    """Save a collection of posts"""
    if not posts:
        return

    collection_dir = os.path.join(config.output_folder, post_type)
    Path(collection_dir).mkdir(parents=True, exist_ok=True)

    for i, post_photos in enumerate(posts, 1):
        post_name = f"{post_type}_Post_{i}"
        post_dir = os.path.join(collection_dir, post_name)
        Path(post_dir).mkdir(parents=True, exist_ok=True)

        logger.info(f"Creating {post_type.lower()} post: {post_name}")

        # Copy photos
        final_paths = []
        for idx, photo_data in enumerate(post_photos):
            original_path = photo_data["path"]
            new_filename = f"{idx+1:02d}_{os.path.basename(original_path)}"
            destination_path = os.path.join(post_dir, new_filename)

            shutil.copy2(original_path, destination_path)
            final_paths.append(destination_path)

        # Generate content
        content = generate_post_content(final_paths, config)
        if content:
            # Add post strategy info
            content["post_strategy"] = {
                "type": post_type,
                "photo_tiers": [p["enhanced"]["tier"] for p in post_photos],
                "categories": [p["enhanced"]["category"] for p in post_photos],
                "avg_score": sum(p["enhanced"]["composite_score"] for p in post_photos)
                / len(post_photos),
            }
            save_post_content(post_dir, content, post_name, config, final_paths)


def generate_analytics_report(photos: List[Dict], posts: Dict, config: Config) -> None:
    """Generate comprehensive analytics report"""
    analytics_dir = os.path.join(config.output_folder, "Analytics")
    Path(analytics_dir).mkdir(parents=True, exist_ok=True)

    report = {
        "summary": {
            "total_photos_analyzed": len(photos),
            "total_posts_created": sum(len(post_list) for post_list in posts.values()),
            "photos_used": sum(
                len(post_list) * config.post_size for post_list in posts.values()
            ),
            "avg_composite_score": (
                sum(p["enhanced"]["composite_score"] for p in photos) / len(photos)
                if photos
                else 0
            ),
        },
        "tier_distribution": {},
        "category_distribution": {},
        "post_strategies": {},
    }

    # Tier distribution
    tiers = [p["enhanced"]["tier"] for p in photos]
    report["tier_distribution"] = dict(Counter(tiers))

    # Category distribution
    categories = [p["enhanced"]["category"] for p in photos]
    report["category_distribution"] = dict(Counter(categories))

    # Post strategies
    for strategy, post_list in posts.items():
        if post_list:
            report["post_strategies"][strategy] = {
                "count": len(post_list),
                "photos_per_post": config.post_size,
                "total_photos": len(post_list) * config.post_size,
            }

    # Save report
    with open(os.path.join(analytics_dir, "enhanced_analytics.json"), "w") as f:
        json.dump(report, f, indent=2)

    # Save readable report
    with open(os.path.join(analytics_dir, "analytics_report.txt"), "w") as f:
        f.write("=== ENHANCED INSTAGRAM ANALYTICS REPORT ===\n\n")
        f.write(
            f"Total Photos Analyzed: {report['summary']['total_photos_analyzed']}\n"
        )
        f.write(f"Total Posts Created: {report['summary']['total_posts_created']}\n")
        f.write(f"Photos Used: {report['summary']['photos_used']}\n")
        f.write(
            f"Average Quality Score: {report['summary']['avg_composite_score']:.2f}/10\n\n"
        )

        f.write("QUALITY TIER DISTRIBUTION:\n")
        for tier, count in report["tier_distribution"].items():
            percentage = (count / len(photos)) * 100
            f.write(f"  {tier.capitalize()}: {count} photos ({percentage:.1f}%)\n")

        f.write("\nCATEGORY DISTRIBUTION:\n")
        for category, count in sorted(
            report["category_distribution"].items(), key=lambda x: x[1], reverse=True
        ):
            percentage = (count / len(photos)) * 100
            f.write(f"  {category.capitalize()}: {count} photos ({percentage:.1f}%)\n")

        f.write("\nPOST STRATEGIES:\n")
        for strategy, info in report["post_strategies"].items():
            f.write(
                f"  {strategy}: {info['count']} posts ({info['total_photos']} photos)\n"
            )

    logger.info("Analytics report generated")


# Keep the original function for backward compatibility


def organize_photos(analyzed_data: List[Dict], config: Config) -> None:
    """Original organize photos function - now calls enhanced version"""
    organize_photos_enhanced(analyzed_data, config)


def get_image_files(source_folder: str) -> List[str]:
    """Get all supported image files from source folder"""
    if not os.path.exists(source_folder):
        raise FileNotFoundError(f"Source folder '{source_folder}' not found")

    image_files = []
    for filename in os.listdir(source_folder):
        if filename.lower().endswith(SUPPORTED_FORMATS):
            image_files.append(os.path.join(source_folder, filename))

    logger.info(f"Found {len(image_files)} image files")
    return image_files
