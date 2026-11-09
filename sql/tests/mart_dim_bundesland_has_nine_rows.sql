-- The region dimension must have exactly the nine Bundesländer, each with population.
SELECT count(*) AS bundeslaender, count(population) AS with_population
FROM mart.dim_bundesland
HAVING count(*) <> 9 OR count(population) NOT IN (0, 9);
