module.exports = function(eleventyConfig) {
  eleventyConfig.addPassthroughCopy("src/assets");

  return {
    dir: {
      input: "src",
      includes: "_includes",
      output: "."
    },
    markdownTemplateEngine: "njk",
    htmlTemplateEngine: "njk"
  };
};
