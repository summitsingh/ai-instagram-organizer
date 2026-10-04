"""Configuration management for the Instagram photo organizer."""
import os
import json
import time
from typing import Dict

from .common import logger, ADVANCED_FEATURES


class Config:
    """Configuration manager for Instagram photo organizer"""
    
    def __init__(self, config_file: str = "config.json"):
        self.config = self.load_config(config_file)
        self._setup_attributes()
    
    def load_config(self, config_file: str) -> Dict:
        """Load configuration from file or create default"""
        default_config = {
            "source_folder": "source_photos",
            "output_folder_prefix": "instagram_posts",
            "temp_convert_folder": "temp_converted_images",
            "ai_provider": "llama",
            "processing": {
                "post_size": 10,
                "process_all": True,
                "dev_mode_limit": 100,
                "similarity_threshold": 3,
                "image_quality": 95,
                "similarity_optimization": {
                    "parallel_workers": 4,
                    "thumbnail_size": 256,
                    "hash_size": 8,
                    "batch_size": 100,
                    "enable_prefilter": True
                }
            },
            "llama": {
                "api_key": "",
                "model": "Llama-4-Maverick-17B-128E-Instruct-FP8",
                "api_url": "https://api.llama.com/v1/chat/completions",
                "timeout": 120
            },
            "ollama": {
                "api_url": "http://localhost:11434/api/generate",
                "model": "gemma3:4b",
                "timeout": 120
            },
            "gemini": {
                "api_key": "",
                "model": "gemini-1.5-flash",
                "api_url": "https://generativelanguage.googleapis.com/v1beta/models"
            },
            "features": {
                "enable_enhancement": True,
                "enable_analytics": True,
                "enable_scheduling": True,
                "enable_multi_platform": True,
                "enable_hashtag_optimization": True
            }
        }
        
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    file_config = json.load(f)
                    self._deep_update(default_config, file_config)
                logger.info(f"Loaded config from {config_file}")
            except Exception as e:
                logger.warning(f"Could not load config file {config_file}: {e}")
        else:
            logger.info(f"Config file {config_file} not found, using defaults")
        
        return default_config
    
    def _deep_update(self, base_dict: Dict, update_dict: Dict):
        """Deep merge dictionaries"""
        for key, value in update_dict.items():
            if isinstance(value, dict) and key in base_dict and isinstance(base_dict[key], dict):
                self._deep_update(base_dict[key], value)
            else:
                base_dict[key] = value
    
    def _setup_attributes(self):
        """Setup config attributes for easy access"""
        self.source_folder = self.config["source_folder"]
        self.output_folder = f"{self.config['output_folder_prefix']}_{int(time.time())}"
        self.temp_convert_folder = self.config["temp_convert_folder"]
        self.ai_provider = self.config["ai_provider"]
        
        # Processing settings
        proc = self.config["processing"]
        self.post_size = proc["post_size"]
        self.process_all = proc["process_all"]
        self.dev_mode_limit = proc["dev_mode_limit"]
        self.similarity_threshold = proc["similarity_threshold"]
        self.image_quality = proc["image_quality"]
        self.keep_temp_files = proc.get("keep_temp_files", False)
        
        # Similarity optimization settings
        sim_opt = proc.get("similarity_optimization", {})
        self.parallel_workers = sim_opt.get("parallel_workers", 4)
        self.thumbnail_size = sim_opt.get("thumbnail_size", 256)
        self.hash_size = sim_opt.get("hash_size", 8)
        self.batch_size = sim_opt.get("batch_size", 100)
        self.enable_prefilter = sim_opt.get("enable_prefilter", True)
        
        # AI provider settings
        self.ollama = self.config["ollama"]
        self.gemini = self.config["gemini"]
        self.llama = self.config.get("llama", {})
        
        # Feature flags (only enable if modules are available)
        features = self.config["features"]
        self.enable_enhancement = features["enable_enhancement"] and ADVANCED_FEATURES.get('image_enhancement', False)
        self.enable_analytics = features["enable_analytics"] and ADVANCED_FEATURES.get('analytics', False)
        self.enable_scheduling = features["enable_scheduling"] and ADVANCED_FEATURES.get('scheduling', False)
        self.enable_multi_platform = features["enable_multi_platform"] and ADVANCED_FEATURES.get('multi_platform', False)
        self.enable_hashtag_optimization = features["enable_hashtag_optimization"] and ADVANCED_FEATURES.get('hashtag_optimizer', False)
        
        # Performance settings
        perf = self.config.get("performance", {})
        ai_perf = perf.get("ai_analysis", {})
        img_perf = perf.get("image_processing", {})
        
        self.ai_parallel_workers = ai_perf.get("parallel_workers", 10)
        self.ai_batch_size = ai_perf.get("batch_size", 8)
        self.ai_max_retries = ai_perf.get("max_retries", 3)
        self.ai_timeout = ai_perf.get("timeout", 30)
        self.enable_caching = ai_perf.get("enable_caching", True)
        self.cache_duration_hours = ai_perf.get("cache_duration_hours", 24)
        
        self.fast_thumbnail_size = img_perf.get("thumbnail_size", 512)
        self.fast_jpeg_quality = img_perf.get("jpeg_quality", 85)
        self.enable_fast_mode = img_perf.get("enable_fast_mode", True)
        
        # Contextual filtering settings
        ctx_filter = self.config.get("contextual_filtering", {})
        self.enable_contextual_filtering = ctx_filter.get("enable_contextual_filtering", True)
        self.contextual_similarity_threshold = ctx_filter.get("contextual_similarity_threshold", 0.7)
        self.contextual_selection_strategy = ctx_filter.get("contextual_selection_strategy", "highest_score")
        self.max_photos_per_context = ctx_filter.get("max_photos_per_context", 3)
        self.min_resolution = img_perf.get("min_resolution", [800, 600])
        self.min_file_size_kb = img_perf.get("min_file_size_kb", 100)
    
    def update_from_args(self, args):
        """Update config from command line arguments"""
        if args.source:
            self.source_folder = args.source
        if args.output:
            self.output_folder = f"{args.output}_{int(time.time())}"
        if args.dev_mode:
            self.process_all = False
        if args.limit:
            self.dev_mode_limit = args.limit
        if args.post_size:
            self.post_size = args.post_size
        if args.similarity is not None:
            self.similarity_threshold = args.similarity
        if args.ai_provider:
            self.ai_provider = args.ai_provider
        if args.llama_key:
            self.llama["api_key"] = args.llama_key
        if args.gemini_key:
            self.gemini["api_key"] = args.gemini_key
        if args.ollama_url:
            self.ollama["api_url"] = args.ollama_url
        if args.ollama_model:
            self.ollama["model"] = args.ollama_model
        if args.simple_mode:
            # Disable advanced features in simple mode
            self.enable_enhancement = False
            self.enable_analytics = False
            self.enable_scheduling = False
            self.enable_multi_platform = False
            self.enable_hashtag_optimization = False
        
        # Performance overrides
        if args.parallel_workers:
            self.ai_parallel_workers = args.parallel_workers
        if args.batch_size:
            self.ai_batch_size = args.batch_size
        if args.fast_mode:
            self.enable_fast_mode = True
        if args.no_cache:
            self.enable_caching = False
        
        # Contextual filtering overrides
        if args.no_contextual_filter:
            self.enable_contextual_filtering = False
        if args.contextual_threshold:
            self.contextual_similarity_threshold = args.contextual_threshold
