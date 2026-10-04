#!/usr/bin/env python3
"""
==================================================
AI Instagram Photos Organizer
==================================================
AI-powered tool to organize photos into Instagram-ready posts with smart captions and hashtags.
Supports both Ollama (local) and Gemini (cloud) AI providers.
==================================================
MIT License
Copyright (c) 2025 Summit Singh Thakur
==================================================
"""

# Thin compatibility shim.
#
# The implementation now lives in the ``organizer`` package (pure move-refactor,
# zero behavior changes). This module re-exports every public name the original
# god-file exposed so existing imports keep working unchanged:
#
#     from ai_instagram_organizer import Config, GeminiRateLimiter, ...
#
# New code should import from ``organizer`` directly.

from organizer import (
    ADVANCED_FEATURES,
    CONVERTED_FORMATS,
    SUPPORTED_FORMATS,
    THUMBNAIL_SIZE,
    TQDM_AVAILABLE,
    Config,
    GeminiRateLimiter,
    HashtagOptimizer,
    LlamaRateLimiter,
    RateLimiter,
    analyze_batch_with_gemini,
    analyze_batch_with_llama,
    analyze_image_with_ai,
    analyze_images_gemini_batched,
    analyze_images_gemini_optimized,
    analyze_images_individually_fallback,
    analyze_images_llama_optimized,
    analyze_images_multi_provider,
    analyze_images_parallel,
    analyze_images_with_load_balancing,
    analyze_photo_patterns,
    analyze_single_image_gemini_direct,
    analyze_single_image_gemini_with_limiter,
    analyze_single_image_llama,
    analyze_with_gemini,
    analyze_with_llama,
    analyze_with_ollama,
    auto_enhance_image,
    calculate_contextual_similarity,
    calculate_diversity_score,
    calculate_text_similarity,
    cleanup_temp_directory,
    compute_image_hash,
    convert_and_prepare_images,
    create_analytics_visualizations,
    create_chronological_posts,
    create_diverse_posts,
    create_platform_variants,
    create_premium_posts,
    create_theme_posts,
    determine_people_count,
    determine_setting,
    determine_time_of_day,
    encode_image_to_base64,
    fast_prefilter_images,
    filter_contextually_similar_images,
    filter_similar_images,
    generate_analytics_report,
    generate_content_with_gemini,
    generate_content_with_llama,
    generate_content_with_ollama,
    generate_post_content,
    generate_posting_schedule,
    get_exif_datetime,
    get_image_files,
    get_image_hash_for_cache,
    get_remaining_photos,
    logger,
    main,
    make_rate_limited_request,
    organize_photos,
    organize_photos_enhanced,
    quick_quality_filter,
    save_post_collection,
    save_post_content,
    select_best_from_context_group,
    select_diverse_photo_set,
    setup_cli_args,
    tqdm,
)
from organizer.common import _analysis_cache, _cache_lock
from organizer.providers import llama as _llama_provider


def __getattr__(name):
    # PEP 562: keep the lazily-rebound _llama_rate_limiter global reachable under
    # this module's historic name. Reads always return the live value.
    if name == "_llama_rate_limiter":
        return _llama_provider._llama_rate_limiter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    main()
